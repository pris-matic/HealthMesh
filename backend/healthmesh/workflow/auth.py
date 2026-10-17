import json
from functools import wraps
from uuid import uuid4

from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.middleware.csrf import get_token


ROLES = ('coordinator', 'reviewer')


def error(code, message, status, field_errors=None):
    return JsonResponse(dict(code=code, message=message, field_errors=field_errors or {}, request_id=str(uuid4())), status=status)


def csrf_failure(request, reason=''):
    return error('csrf_failed', 'Refresh the session and try again.', 403)


def api_not_found(request, unmatched_path=''):
    return error('not_found', 'API endpoint not found.', 404)


def body(request):
    try:
        value = json.loads(request.body or b'{}')
    except (ValueError, UnicodeError):
        raise ValueError('A JSON object is required.')
    if not isinstance(value, dict):
        raise ValueError('A JSON object is required.')
    return value


def role_for(user):
    return next((role for role in ROLES if user.groups.filter(name=role).exists()), None)


def api(methods, roles=ROLES):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                return error('method_not_allowed', 'Method not allowed.', 405)
            if not request.user.is_authenticated or not request.user.is_active:
                return error('authentication_required', 'Sign in to continue.', 401)
            if not request.user.groups.filter(name__in=roles).exists():
                return error('forbidden', 'Your role cannot perform this action.', 403)
            try:
                return view(request, *args, **kwargs)
            except ValueError as exc:
                return error('invalid_request', str(exc), 400)
        return wrapped
    return decorate


def session(request):
    if request.method == 'POST':
        try:
            data = body(request)
        except ValueError as exc:
            return error('invalid_request', str(exc), 400)
        if not isinstance(data.get('username'), str) or not isinstance(data.get('password'), str):
            return error('invalid_request', 'Username and password are required.', 400)
        user = authenticate(request, username=data['username'], password=data['password'])
        if user is None or role_for(user) is None:
            return error('invalid_credentials', 'Invalid demo credentials.', 401)
        login(request, user)
    elif request.method == 'DELETE':
        if not request.user.is_authenticated or role_for(request.user) is None:
            return error('authentication_required', 'Sign in to continue.', 401)
        logout(request)
    elif request.method != 'GET':
        return error('method_not_allowed', 'Method not allowed.', 405)
    user = request.user
    return JsonResponse({'user': {'username': user.username, 'role': role_for(user)} if user.is_authenticated else None,
                         'csrf_token': get_token(request)})
