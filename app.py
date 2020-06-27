"""
    Slack Moderator

    Slack bot that evaluates messages and attachments against moderatecontent.com API

"""

import os
import sys
import logging
import requests
import json_logging
from flask import Flask
from flask.logging import create_logger
from slack import WebClient
from slack.errors import SlackApiError
from flask_cors import CORS
from dotenv import load_dotenv
from slackeventsapi import SlackEventAdapter


load_dotenv()

DEBUG = os.environ.get('DEBUG', False)
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'DEBUG')
REPLACE_TEXT = os.environ.get('REPLACE_TEXT', '***')
BAD_MESSAGE_POST = 'Your message `{:s}` was removed because it had bad words.'

SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN')
SLACK_ADMIN_TOKEN = os.environ.get('SLACK_ADMIN_TOKEN')
SLACK_SIGNING_SECRET = os.environ.get('SLACK_SIGNING_SECRET')
MODERATE_CONTENT_KEY = os.environ.get('MODERATE_CONTENT_KEY')

logging.basicConfig(level=LOG_LEVEL)

app = Flask('slackmoderator')
logger = create_logger(app)

if not all([SLACK_SIGNING_SECRET, MODERATE_CONTENT_KEY, SLACK_BOT_TOKEN]):
    logger.critical('SLACK_SIGNING_SECRET, MODERATE_CONTENT_KEY, SLACK_BOT_TOKEN must be set.')
    sys.exit(1)

if not DEBUG:
    json_logging.init_flask(enable_json=True)
    json_logging.init_request_instrument(app)

slack_client = WebClient(SLACK_BOT_TOKEN)
slack_admin_client = WebClient(SLACK_ADMIN_TOKEN)
slack_events_adapter = SlackEventAdapter(SLACK_SIGNING_SECRET, "/", server=app)
CORS(app)


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
    app.run(port=os.environ.get('PORT', 3000), debug=DEBUG)
