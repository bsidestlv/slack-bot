""" CTFd Helper Class."""
import os
import sys
import logging

from collections import namedtuple

import requests

from dotenv import load_dotenv
from slack.errors import SlackApiError
from requests.compat import urljoin
from redis_collections import List, Dict

load_dotenv(verbose=True)

CTFD_TOKEN = os.getenv('CTFD_TOKEN')
CTFD_CHANNELS = os.getenv('CTFD_CHANNELS').split(',')
REQUIRED_KEYS = [CTFD_TOKEN]

CTFdConfig = namedtuple('CTFdConfig', ['post_solve', 'post_solve_img', 'post_solve_only_top10',
                                       'post_first_blood', 'post_first_blood_img',
                                       'post_place_change', 'post_place_change_img'])
CTFdCache = namedtuple('CTFdCache', ['users', 'teams', 'solves'])
CTFdSolve = namedtuple('CTFdSolve', ['clng', 'user', 'team', 'team_old'])
TOP10 = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th', '9th', '10th']
TOP10_EMOJI = {'1st': ':first_place_medal:',
               '2nd': ':second_place_medal:',
               '3rd': ':third_place_medal:'}

BLOCKS = [
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": ""  # REPLACE THIS
        },
        "accessory": {
            "type": "image",
            "image_url": "",  # REPLACE THIS
            "alt_text": ""  # REPLACE THIS
        }
    },
    {
        "type": "divider"
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": ""  # REPLACE THIS
        }
    }
]

class CTFd():
    """Blocks template."""
    config = CTFdConfig(
        post_solve=os.getenv('CTFD_POST_SOLVE'),
        post_solve_only_top10=os.getenv('CTFD_POST_SOLVE_ONLY_TOP10'),
        post_solve_img=os.getenv('CTFD_POST_SOLVE_IMG', 'https://i.imgur.com/SdvQx2F.jpg'),
        post_first_blood=os.getenv('CTFD_POST_FIRST_BLOOD'),
        post_first_blood_img=os.getenv('CTFD_POST_FIRST_BLOOD_IMG', 'https://i.imgur.com/eLm2JG3.jpg'),
        post_place_change=os.getenv('CTFD_POST_PLACE_CHANGE'),
        post_place_change_img=os.getenv('CTFD_POST_PLACE_CHANGE_IMG', 'https://i.imgur.com/SdvQx2F.jpg'),
    )

    _api = requests.Session()
    solve = None
    blocks = BLOCKS.copy()
    _logger = logging.getLogger(__name__)

    def __init__(self, app, base_url, redis, slack_client):
        """ Init. """
        if not all(REQUIRED_KEYS):
            self._logger.critical('%s must be set.', REQUIRED_KEYS)
            sys.exit(1)

        self._base_url = base_url
        self.cache = CTFdCache(solves=List(key='ctfd_submission_db', redis=redis),
                               teams=Dict(key='ctfd_teams', redis=redis),
                               users=Dict(key='ctfd_users', redis=redis))
        self._api.headers.update({'Authorization': f'Token {CTFD_TOKEN}', 'Content-type': 'application/json'})
        self._slack = slack_client

        self.bind_route(app)

    def _request(self, method, url, *args, **kwargs):
        """ Send request to CTFd. """
        url = urljoin(self._base_url, url)
        res = self._api.request(method.upper(), url, *args, **kwargs)
        res.raise_for_status()
        self._logger.debug('CTFd: Fetch: %s', res.text)
        res = res.json()
        if not res.get('success'):
            raise Exception(f'CTFd: Request failed - {res.text}')

        return res.get('data')

    def _get(self, typ, i, cache=True, obj=None):
        """ Get CTFd User Name from ID. """
        if cache:
            obj = getattr(self.cache, typ).get(i)
        if not obj:
            obj = self._request('GET', f'{typ}/{i}')
            obj['lnk'] = f"<https://ctf20.bsidestlv.com/{typ}/{obj['id']}|{obj['name']}>"
            getattr(self.cache, typ)[i] = obj
        return obj

    def check_solve(self, new_solve):
        """ Get basic info and set solve template. """
        solve = CTFdSolve(
            clng=new_solve['challenge'],
            user=self._get('users', new_solve['user_id']),
            team_old=self._get('teams', new_solve['team_id']),  # We need the old place for later
            team=self._get('teams', new_solve['team_id'], cache=False),
        )
        solve.clng['lnk'] = "<https://ctf20.bsidestlv.com/challenges#{name}|{name}>".format(**solve.clng)

        self._logger.debug(solve)

        _fmt = self.cache._asdict()
        _fmt.update(solve._asdict())

        post_to_slack = False

        if self.config.post_solve:
            self.blocks[0]['text']['text'] = ":flags: {user[lnk]} (Team: {team[lnk]}) just solved {clng[lnk]} and got *{clng[value]}* points!".format(**_fmt)
            self.blocks[0]['accessory']['image_url'] = self.config.post_solve_img
            self.blocks[0]['accessory']['alt_text'] = 'Challenge solved!'
            self.blocks[2]['text']['text'] = ":medal: Team {team[lnk]} is ranked *{team[place]}* with {team[score]} points total!".format(**_fmt)
            if self.config.post_solve_only_top10 and solve.team['place'] not in TOP10:
                post_to_slack = False

        if self.config.post_first_blood:
            if not self.cache.solves or not any([s for s in self.cache.solves if s.get('challenge_id') == new_solve['challenge_id']]):
                post_to_slack = True
                self.blocks[0]['text']['text'] = "*First blood!!!*\n\n{user[lnk]} (Team: {team[lnk]}) is _*first*_ to solve {clng[lnk]}".format(**_fmt)
                self.blocks[0]['accessory']['image_url'] = self.config.post_first_blood_img
                self.blocks[0]['accessory']['alt_text'] = 'First blood!'
                self.blocks[2]['text']['text'] = ":medal: Team {team[lnk]} is now ranked *{team[place]}* with {team[score]} points!".format(**_fmt)

        if self.config.post_place_change:
            if solve.team['place'] in TOP10 and solve.team['place'] != solve.team_old['place']:
                post_to_slack = True
                _fmt['place_emoji'] = ':medal:'
                if solve.team['place'] in TOP10_EMOJI.keys():
                    _fmt['place_emoji'] = TOP10_EMOJI[solve.team['place']]
                self.blocks[0]['text']['text'] = ":flags: {user[lnk]} (Team: {team[lnk]}) just solved {clng[lnk]} and got *{clng[value]}* points!".format(**_fmt)
                self.blocks[0]['accessory']['image_url'] = self.config.post_place_change_img
                self.blocks[0]['accessory']['alt_text'] = 'First blood!'
                self.blocks[2]['text']['text'] = "{place_emoji} Team: {team[lnk]} just moved from *{team_old[place]}* to *{team[place]}* place!".format(**_fmt)

        return post_to_slack

    def bind_route(self, app):
        """ Bind route, to be called on a schedule. """
        @app.route('/ctfd_cron')
        def _check_solves():
            new_solves = self._request('GET', 'submissions', params={'type': 'correct'})
            diff = len(new_solves) - len(self.cache.solves)
            if not diff:
                return {'status': 'noop'}
            self._logger.debug('CTFd: Got %d new solves!', diff)
            for solve in new_solves[-diff:]:
                self._logger.debug(solve)
                if self.check_solve(solve):
                    try:
                        for channel in CTFD_CHANNELS:
                            self._slack.chat_postMessage(channel=channel, blocks=self.blocks)
                    except SlackApiError as exc:
                        assert exc.response["ok"] is False
                        assert exc.response["error"]  # str like 'invalid_auth', 'channel_not_found'
                        self._logger.error('Failed due to %s', exc.response['error'])
            self.cache.solves.extend(new_solves[-diff:])
            return {'status': 'ok'}
