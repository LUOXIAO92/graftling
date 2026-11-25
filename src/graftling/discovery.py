"""Progressive agent features over the existing action schema and handler."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable

from graftling.schema import ValidationErrors, schema_errors
from graftling.tool import ACTION_FIELDS, INPUT_SCHEMA as ACTION_SCHEMA, AgentsResult


INPUT_SCHEMA: dict[str, Any] = {
    'type': 'object',
    'properties': {
        'action': {'type': 'string', 'enum': ['discover', 'describe', 'execute']},
        'query': {'type': 'string'},
        'feature': {'type': 'string'},
        'schema': {'type': 'boolean', 'default': False,
                   'description': 'For describe only: return the selected input schema.'},
        'arguments': {'type': 'object',
                      'description': 'For execute: the feature arguments. With arguments_file, these fields '
                                     'replace the same top-level fields of the file.'},
        'arguments_file': {'type': 'string',
                           'description': 'For execute: path of a UTF-8 JSON file holding the arguments object, '
                                          'instead of or beside arguments. It must lie inside the directory your '
                                          'host lets you read (usually your workspace; a relative path starts '
                                          'there). After an error, fix the file and call again.'},
    },
    'required': ['action'],
    'additionalProperties': False,
}

DESCRIPTIONS = {
    'create': 'Create a child Agent on a Runtime; returns before its work finishes.',
    'models': "List a Runtime's configured connections with their providers and models, grouped by provider; "
              'read only.',
    'send': 'Send a message to an Agent.',
    'status': 'Read Agent status, or the visible hierarchy when no Agent is supplied.',
    'wait': 'Wait a finite positive number of seconds for an Agent.',
    'interrupt': 'Stop active work while retaining the native Session for continuation.',
    'resume': 'Continue the same native Session, optionally with a message.',
    'requests': 'Read pending native approval or input requests.',
    'reply': 'Answer one native request explicitly.',
    'retire': 'Remove an Agent from the active list while retaining its history.',
    'ask_parent': 'Ask your direct parent for a decision or resources (more time, wider scope) '
                  'and wait a finite time for its answer.',
    'session_read': "Read or search the archived Session of yourself, an ancestor or a descendant: a Python "
                    'slice of its ordered events (default the last 5, conversation text only).',
}

# Intent words a caller may use instead of the action name; discovery matches them too.
INTENTS = {
    'create': 'start launch spawn new run begin',
    'models': 'providers catalog available choose connections',
    'send': 'message tell',
    'status': 'list show',
    'interrupt': 'stop cancel',
    'requests': 'approvals pending',
    'reply': 'answer approve respond',
    'retire': 'remove delete archive',
    'ask_parent': 'request permission time budget scope extend',
    'session_read': 'history transcript conversation log events grep page',
}

# Words that carry no intent; they would otherwise match nearly every description.
FILLER = frozenset({'a', 'an', 'the', 'to', 'of', 'for', 'my', 'one', 'please'})


# Largest arguments file read, in bytes; argument documents are prose and small objects.
ARGUMENTS_FILE_LIMIT = 1 << 20


def envelope_errors(request: Any, *, files: Path | None = None) -> list[dict[str, str]]:
    """Return every problem of the discover/describe/execute envelope itself.

    ``files`` is the directory the host lets this caller read arguments files
    from; without it ``arguments_file`` is refused.
    """
    errors = schema_errors(request, INPUT_SCHEMA, 'request')
    if not isinstance(request, dict) or request.get('action') not in {'discover', 'describe', 'execute'}:
        return errors

    action = request['action']
    fields = {'action', 'query'} if action == 'discover' else {'action', 'feature'}
    if action == 'execute':
        fields |= {'arguments', 'arguments_file'}
    if action == 'describe':
        fields.add('schema')
    reported = {error['path'] for error in errors}
    for name in sorted(request.keys() - fields):
        if f'request.{name}' not in reported:
            errors.append({'path': f'request.{name}', 'reason': f'not valid for {action}'})
    if action != 'discover' and 'feature' not in request:
        errors.append({'path': 'request.feature', 'reason': 'required'})
    if action == 'execute' and not {'arguments', 'arguments_file'} & request.keys():
        errors.append({'path': 'request.arguments', 'reason': 'required (or arguments_file)'})
    if action == 'execute' and 'arguments_file' in request and files is None:
        errors.append({'path': 'request.arguments_file', 'reason': 'this host accepts no arguments file'})
    return errors


def validate_envelope(request: dict, *, files: Path | None = None) -> str:
    """Validate the shared discover/describe/execute envelope, returning its action.

    Raises :class:`ValidationErrors` listing every envelope problem. ``files``
    is as in :func:`envelope_errors`; it only decides whether
    ``arguments_file`` may be used, :func:`read_arguments` reads it.
    """
    errors = envelope_errors(request, files=files)
    if errors:
        raise ValidationErrors(errors)
    return request['action']


def read_arguments(request: dict, files: Path | None) -> dict:
    """Return an execute request's arguments, reading ``arguments_file`` if given.

    The file's object comes first and inline ``arguments`` replace its
    top-level fields. The path, relative ones from ``files``, must resolve
    (links followed) to a regular file inside ``files`` of at most
    ``ARGUMENTS_FILE_LIMIT`` bytes. The content is only data: errors name the
    path the caller gave and, for invalid JSON, a position, never the content.

    Raises
    ------
    ValidationErrors
        One ``request.arguments_file`` problem.
    """
    inline = request.get('arguments', {})
    if 'arguments_file' not in request:
        return inline

    def refuse(reason: str) -> ValidationErrors:
        """One error for the file the caller named."""
        return ValidationErrors([{'path': 'request.arguments_file', 'reason': reason}])

    if files is None:
        raise refuse('this host accepts no arguments file')
    named = request['arguments_file']
    # Containment is checked on the resolved path before anything else, so a
    # path outside reveals nothing about what exists there.
    root = Path(files).resolve()
    path = (root / named).resolve()
    if not path.is_relative_to(root):
        raise refuse(f'{named} is outside the directory this caller may read ({root})')
    # O_NOFOLLOW refuses a symbolic link as the opened name. Windows has no such
    # flag, so there the opened file must be the regular file lstat saw first.
    nofollow = getattr(os, 'O_NOFOLLOW', None)
    try:
        seen       = os.lstat(path) if nofollow is None else None
        descriptor = os.open(path, os.O_RDONLY | (nofollow or 0) | getattr(os, 'O_BINARY', 0))
    except FileNotFoundError:
        raise refuse(f'{named} does not exist') from None
    except OSError as error:
        raise refuse(f'{named} cannot be opened ({error.strerror})') from None
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or (seen is not None and (
            not stat.S_ISREG(seen.st_mode) or (seen.st_dev, seen.st_ino) != (opened.st_dev, opened.st_ino))):
        os.close(descriptor)
        raise refuse(f'{named} is not a regular file')
    with os.fdopen(descriptor, 'rb') as stream:
        data = stream.read(ARGUMENTS_FILE_LIMIT + 1)
    if len(data) > ARGUMENTS_FILE_LIMIT:
        raise refuse(f'{named} is larger than {ARGUMENTS_FILE_LIMIT} bytes')
    try:
        document = json.loads(data.decode('utf-8'))
    except UnicodeDecodeError:
        raise refuse(f'{named} is not UTF-8 text') from None
    except json.JSONDecodeError as error:
        raise refuse(f'{named} is not valid JSON (line {error.lineno}, column {error.colno})') from None
    if not isinstance(document, dict):
        raise refuse(f'{named} must hold a JSON object')
    return {**document, **inline}


def feature_schema(feature: str) -> dict:
    """Project one action's schema without exposing parameters of other actions."""
    required, optional = ACTION_FIELDS[feature]
    fields = required | optional
    return {
        'type': 'object',
        'properties': {name: deepcopy(schema) for name, schema in ACTION_SCHEMA['properties'].items()
                       if name in fields},
        'required': sorted(required),
        'additionalProperties': False,
    }


def handle_request(
    request: dict,
    execute: Callable[[dict], AgentsResult],
    *,
    files: Path | None = None,
) -> AgentsResult:
    """Discover one action or invoke the host's already-bound business callback.

    The callback is a host input, never a model argument. No identity, state,
    controller or Runtime is created by discovery or schema requests.
    ``files`` is the directory the host lets this caller read an
    ``arguments_file`` from (its verified workspace); ``None`` refuses one.
    Every envelope and argument problem is returned in one result.
    """
    try:
        errors = envelope_errors(request, files=files)
        action = request.get('action') if isinstance(request, dict) else None
        feature = request.get('feature') if isinstance(request, dict) else None
        if action in {'describe', 'execute'} and isinstance(feature, str) and feature not in ACTION_FIELDS:
            errors.append({'path': 'request.feature', 'reason': f'unknown feature {feature}'})
        arguments = None
        if action == 'execute' and feature in ACTION_FIELDS and not any(
                error['path'] in {'request.arguments', 'request.arguments_file'} for error in errors):
            try:
                arguments = read_arguments(request, files)
            except ValidationErrors as error:
                errors += error.errors
            else:
                errors += schema_errors(arguments, feature_schema(feature), 'arguments')
        if errors:
            raise ValidationErrors(errors)

        if action == 'discover':
            terms = [term for term in request.get('query', '').casefold().split() if term not in FILLER]
            return AgentsResult({'features': [
                {'feature': name, 'description': description}
                for name, description in DESCRIPTIONS.items()
                if all(term in f'{name} {INTENTS.get(name, "")} {description}'.casefold() for term in terms)
            ], 'describe': {'action': 'describe', 'feature': '<feature>'}})
        schema = feature_schema(feature)
        if action == 'describe':
            if request.get('schema', False):
                return AgentsResult({'input_schema': schema})
            return AgentsResult({'feature': feature, 'description': DESCRIPTIONS[feature],
                                 'input_schema': schema})
        return execute({'action': feature, **arguments})
    except ValidationErrors as error:
        action = request.get('action') if isinstance(request, dict) else None
        return AgentsResult({'action': action, 'error': {'code': 'invalid-input', 'message': str(error),
                                                         'errors': error.errors}}, failed=True)
    except ValueError as error:
        action = request.get('action') if isinstance(request, dict) else None
        return AgentsResult({'action': action, 'error': {'code': 'invalid-input', 'message': str(error)}},
                            failed=True)
