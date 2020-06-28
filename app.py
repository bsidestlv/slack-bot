"""
    BSidesTLV Slack Bot

    * Moderator: Evaluates messages and attachments against moderatecontent.com API
    * CTFd: Announces new solves
"""

import os
import sys
import logging

import redis
import requests
import json_logging

from slack import WebClient
from slack.errors import SlackApiError
from dotenv import load_dotenv
from flask import Flask
from flask.logging import create_logger
from flask_cors import CORS
from requests.compat import urljoin
from slackeventsapi import SlackEventAdapter
from redis_collections import List


load_dotenv()

PORT = int(os.getenv('PORT', '3000'))
DEBUG = os.getenv('DEBUG')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'DEBUG')
REPLACE_TEXT = os.getenv('REPLACE_TEXT', '***')
BAD_MESSAGE_POST = 'Your message `{:s}` was removed because it had bad words.'

REDIS_URL = os.getenv('REDIS_URL')
CTFD_TOKEN = os.getenv('CTFD_TOKEN')
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SLACK_ADMIN_TOKEN = os.getenv('SLACK_ADMIN_TOKEN')
SLACK_SIGNING_SECRET = os.getenv('SLACK_SIGNING_SECRET')
MODERATE_CONTENT_KEY = os.getenv('MODERATE_CONTENT_KEY')
CTFD_CHANNELS = os.getenv('CTFD_CHANNELS').split(',')

logging.basicConfig(level=LOG_LEVEL)

app = Flask('slackmoderator')
CORS(app)

logger = create_logger(app)

REQUIRED_KEYS = [REDIS_URL, SLACK_SIGNING_SECRET, MODERATE_CONTENT_KEY, SLACK_BOT_TOKEN, CTFD_TOKEN]
if not all(REQUIRED_KEYS):
    logger.critical('%s must be set.', REQUIRED_KEYS)
    sys.exit(1)

if not DEBUG:
    json_logging.init_flask(enable_json=True)
    json_logging.init_request_instrument(app)

slack_client = WebClient(SLACK_BOT_TOKEN)
slack_admin_client = WebClient(SLACK_ADMIN_TOKEN)
slack_events_adapter = SlackEventAdapter(SLACK_SIGNING_SECRET, "/", server=app)

ctfd_client = requests.Session()
ctfd_client.headers.update({'Authorization': f'Token {CTFD_TOKEN}'})
ctfd_client.headers.update({'Content-type': 'application/json'})

r = redis.Redis().from_url(REDIS_URL)
submission_db = List(key='ctfd_submission_db', redis=r)
logger.debug(f'Loading submission_db {list(submission_db)}')

def ctfd_request(method, url, *args, **kwargs):
    """ Send request to CTFd. """
    url = urljoin('https://ctf20.bsidestlv.com/api/v1/', url)
    res = ctfd_client.request(method.upper(), url, *args, **kwargs)
    res.raise_for_status()
    logger.debug('CTFd: Fetch: %s', res.text)
    res = res.json()
    if not res.get('success'):
        raise Exception(f'CTFd: Request failed - {res.text}')
    return res.get('data')

def ctfd_get_user(i):
    """ Get CTFd User Name from ID. """
    logger.debug('CTFd: Fetching user %d', i)
    return ctfd_request('GET', f'users/{i}')

def ctfd_get_team(i):
    """ Get CTFd User Name from ID. """
    logger.debug('CTFd: Fetching team %d', i)
    return ctfd_request('GET', f'teams/{i}')

@app.route('/cron')
def check_solves():
    """ Check for new solves every minute and post to slack. """
    global submission_db

    submissions = ctfd_request('GET', 'submissions', params={'type': 'correct'})
    if len(submissions) > len(submission_db):
        diff = len(submissions)-len(submission_db)
        logger.debug('CTFd: Got %d new solves!', diff)
        for solve in submissions[len(submission_db):]:
            user = ctfd_get_user(solve['user'])
            user_lnk = f"<https://ctf20.bsidestlv.com/users/{user.get('id')}|{user.get('name')}>"
            team = ctfd_get_team(solve['team'])
            team_lnk = f"<https://ctf20.bsidestlv.com/teams/{team.get('id')}|{team.get('name')}>"
            clng = solve['challenge']
            clng_link = f"<https://ctf20.bsidestlv.com/challenges/#{clng.get('name')}|{clng.get('name')}>"
            logger.debug(solve)
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*First blood!!!*\n\n{user_lnk} (Team: {team_lnk}) is _*first*_ to solve {clng_link}"
                    },
                    "accessory": {
                        "type": "image",
                        "image_url": "https://i.imgur.com/eLm2JG3.jpg",
                        "alt_text": "First Blood!!"
                    }
                },
                {
                    "type": "divider"
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f":medal: Team {team_lnk} is now ranked *{team.get('place')}* with {team.get('score')} points!"
                        }
                    ]
                }
            ]

            if not len(submission_db) or any([sub for sub in submission_db if sub.get('challenge_id') == solve['challenge_id']]):
                blocks[0]['text']['text'] = f":flags: {user_lnk} (Team: {team.get('name')}) just solved {clng_link}"
                blocks[0]['accessory']['image_url'] = "https://i.imgur.com/SdvQx2F.jpg"
                blocks[0]['accessory']['alt_text'] = "Challenge Solved!"
                blocks.pop() # remove extra section
                blocks.pop() # remove extra section

            blocks[0]['text']['text'] += f" and got *{clng.get('value')}* points!"
            try:
                for channel in CTFD_CHANNELS:
                    slack_client.chat_postMessage(channel=channel, blocks=blocks)
            except SlackApiError as exc:
                assert exc.response["ok"] is False
                assert exc.response["error"]  # str like 'invalid_auth', 'channel_not_found'
                logger.error('Failed due to %s', exc.response['error'])
        submission_db.extend(submissions[len(submission_db):])
        logger.debug(f'Updated submission_db {submission_db} {list(submission_db)}')
    return {'ok': True}

def eval_text(event, changed=False):
    """ Evaluate text against moderatecontent and replace message if needed. """
    data = {'key': MODERATE_CONTENT_KEY,
            'replace': REPLACE_TEXT}
    if changed:
        timestamp = event['message']['ts']
        user = event['message']['user']
        data['msg'] = event['message']['text']
    else:
        timestamp = event['ts']
        user = event['user']
        data['msg'] = event['text']

    logger.debug('Sending %s for evaluation', data)
    res = requests.get('https://api.moderatecontent.com/text/', params=data)
    res.raise_for_status()
    mod = res.json()
    logger.debug('moderatecontent response: %s', mod)
    if mod.get('bad_words'):
        logger.error('FOUND A BAD MESSAGE ! %s', event)
        slack_admin_client.chat_delete(channel=event['channel'], ts=timestamp)
        slack_client.chat_postEphemeral(channel=event['channel'], user=user, as_authed=True,
                                        text=BAD_MESSAGE_POST.format(mod['clean']))

@slack_events_adapter.on("message")
def handle_message(message):
    """ Respond to message event """
    event = message.get('event')
    logger.debug('Event: %s', event)
    try:
        if event.get('text'):
            eval_text(event, event.get('subtype') == 'message_changed')
    except SlackApiError as exc:
        assert exc.response["ok"] is False
        assert exc.response["error"]  # str like 'invalid_auth', 'channel_not_found'
        logger.error('Failed due to %s', exc.response['error'])

if __name__ == '__main__':
    app.run(port=PORT, debug=DEBUG)
