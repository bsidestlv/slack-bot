"""
    BSidesTLV Slack Bot

    * Moderator: Evaluates messages and attachments against moderatecontent.com API
    * CTFd: Announces new solves
"""

import os
import sys
import logging

import redis
import json_logging

from slack import WebClient
from dotenv import load_dotenv
from flask import Flask
from flask.logging import create_logger
from flask_cors import CORS
from slackeventsapi import SlackEventAdapter

from ctfd import CTFd

load_dotenv()

PORT = int(os.getenv('PORT', '3000'))
DEBUG = os.getenv('DEBUG')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'DEBUG')
REPLACE_TEXT = os.getenv('REPLACE_TEXT', '***')
BAD_MESSAGE_POST = 'Your message `{:s}` was removed because it had bad words.'

REDIS_URL = os.getenv('REDIS_URL')
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SLACK_ADMIN_TOKEN = os.getenv('SLACK_ADMIN_TOKEN')
SLACK_SIGNING_SECRET = os.getenv('SLACK_SIGNING_SECRET')
MODERATE_CONTENT_KEY = os.getenv('MODERATE_CONTENT_KEY')

logging.basicConfig(level=LOG_LEVEL)

app = Flask('slackmoderator')
with app.app_context():
    logger = create_logger(app)

    REQUIRED_KEYS = [REDIS_URL, SLACK_SIGNING_SECRET, MODERATE_CONTENT_KEY, SLACK_BOT_TOKEN]
    if not all(REQUIRED_KEYS):
        logger.critical('%s must be set.', REQUIRED_KEYS)
        sys.exit(1)

    if not DEBUG:
        json_logging.init_flask(enable_json=True)
        json_logging.init_request_instrument(app)

    slack_client = WebClient(SLACK_BOT_TOKEN)
    slack_admin_client = WebClient(SLACK_ADMIN_TOKEN)
    slack_events_adapter = SlackEventAdapter(SLACK_SIGNING_SECRET, "/", server=app)


    r = redis.Redis().from_url(REDIS_URL)
    CORS(app)
    ctfd = CTFd(app, base_url='https://ctf20.bsidestlv.com/api/v1/', redis=r, slack_client=slack_client)

if __name__ == '__main__':
    app.run(port=PORT, debug=DEBUG)
