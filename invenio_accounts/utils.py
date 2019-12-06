# -*- coding: utf-8 -*-
#
# This file is part of Invenio.
# Copyright (C) 2017-2018 CERN.
#
# Invenio is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""Utility function for ACCOUNTS."""

import uuid
from datetime import datetime

import six
from flask import current_app, request, session, url_for
from flask_security import current_user
from flask_security.confirmable import generate_confirmation_token
from flask_security.recoverable import generate_reset_password_token
from flask_security.signals import user_registered
from flask_security.utils import config_value as security_config_value
from flask_security.utils import get_security_endpoint_name, hash_password, \
    send_mail
from future.utils import raise_from
from jwt import DecodeError, ExpiredSignatureError, decode, encode
from six.moves.urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from werkzeug.routing import BuildError
from werkzeug.utils import import_string

from .errors import JWTDecodeError, JWTExpiredToken
from .proxies import current_datastore, current_security


def jwt_create_token(user_id=None, additional_data=None):
    """Encode the JWT token.

    :param int user_id: Addition of user_id.
    :param dict additional_data: Additional information for the token.
    :returns: The encoded token.
    :rtype: str

    .. note::
        Definition of the JWT claims:

        * exp: ((Expiration Time) expiration time of the JWT.
        * sub: (subject) the principal that is the subject of the JWT.
        * jti: (JWT ID) UID for the JWT.
    """
    # Create an ID
    uid = str(uuid.uuid4())
    # The time in UTC now
    now = datetime.utcnow()
    # Build the token data
    token_data = {
        'exp': now + current_app.config['ACCOUNTS_JWT_EXPIRATION_DELTA'],
        'sub': user_id or current_user.get_id(),
        'jti': uid,
    }
    # Add any additional data to the token
    if additional_data is not None:
        token_data.update(additional_data)

    # Encode the token and send it back
    encoded_token = encode(
        token_data,
        current_app.config['ACCOUNTS_JWT_SECRET_KEY'],
        current_app.config['ACCOUNTS_JWT_ALOGORITHM']
    ).decode('utf-8')
    return encoded_token


def jwt_decode_token(token):
    """Decode the JWT token.

    :param str token: Additional information for the token.
    :returns: The token data.
    :rtype: dict
    """
    try:
        return decode(
            token,
            current_app.config['ACCOUNTS_JWT_SECRET_KEY'],
            algorithms=[
                current_app.config['ACCOUNTS_JWT_ALOGORITHM']
            ]
        )
    except DecodeError as exc:
        raise_from(JWTDecodeError(), exc)
    except ExpiredSignatureError as exc:
        raise_from(JWTExpiredToken(), exc)


def set_session_info(app, response, **extra):
    """Add X-Session-ID and X-User-ID to http response."""
    session_id = getattr(session, 'sid_s', None)
    if session_id:
        response.headers['X-Session-ID'] = session_id
    if current_user.is_authenticated:
        response.headers['X-User-ID'] = current_user.get_id()


def obj_or_import_string(value, default=None):
    """Import string or return object.

    :params value: Import path or class object to instantiate.
    :params default: Default object to return if the import fails.
    :returns: The imported object.
    """
    if isinstance(value, six.string_types):
        return import_string(value)
    elif value:
        return value
    return default


def _generate_token_url(endpoint, token):
    try:
        url = url_for(endpoint, token=token, _external=True)
    except BuildError:
        # Try to parse URL and build
        scheme, netloc, path, query, fragment = urlsplit(endpoint)
        scheme = scheme or request.scheme
        netloc = netloc or request.host
        assert netloc
        qs = parse_qs(query)
        qs['token'] = token
        query = urlencode(qs)
        url = urlunsplit((scheme, netloc, path, query, fragment))
    return url


def default_reset_password_link_func(user):
    """Return the confirmation link that will be sent to a user via email."""
    token = generate_reset_password_token(user)
    endpoint = current_app.config['ACCOUNTS_RESET_PASSWORD_ENDPOINT'] or \
        get_security_endpoint_name('reset_password')
    return token, _generate_token_url(endpoint, token)


def default_confirmation_link_func(user):
    """Return the confirmation link that will be sent to a user via email."""
    token = generate_confirmation_token(user)
    endpoint = current_app.config['ACCOUNTS_CONFIRM_EMAIL_ENDPOINT'] or \
        get_security_endpoint_name('confirm_email')
    return token, _generate_token_url(endpoint, token)


def register_user(_confirmation_link_func=None, **user_data):
    """Register a user."""
    confirmation_link_func = _confirmation_link_func or \
        default_confirmation_link_func
    user_data['password'] = hash_password(user_data['password'])
    user = current_datastore.create_user(**user_data)
    current_datastore.commit()

    token, confirmation_link = None, None
    if current_security.confirmable and user.confirmed_at is None:
        token, confirmation_link = confirmation_link_func(user)

    user_registered.send(
        current_app._get_current_object(), user=user, confirm_token=token)

    if security_config_value('SEND_REGISTER_EMAIL'):
        send_mail(security_config_value('EMAIL_SUBJECT_REGISTER'), user.email,
                  'welcome', user=user, confirmation_link=confirmation_link)

    return user
