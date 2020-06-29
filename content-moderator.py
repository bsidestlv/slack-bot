""" TODO:
finish this shit

"""

'''
@slack_events_adapter.on("message")
def handle_message(message):
    """ Respond to message event """
    event = message.get('event')
    logger.debug('Event: %s', event)
    try:
        if event.get('text'):
            # eval_text(event, event.get('subtype') == 'message_changed')
            pass
    except SlackApiError as exc:
        assert exc.response["ok"] is False
        assert exc.response["error"]  # str like 'invalid_auth', 'channel_not_found'
        logger.error('Failed due to %s', exc.response['error'])


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
'''