"""
Interfaces to subversion archives, via both filesystem (`svnadmin`, `svnlook`) and HTTP
server (`svn`, `svnmucc`).

TODO: When api.process.run_command is updated to return the `ProcessOutput` data
structure, in cases here where `data` is being added to the structure before return,
instead return the data separately, or None if not created. This will change the
functions' interface and so also require some refactoring in the consumers.
"""

import os
import re
import tempfile
from lxml import etree
from pathlib import Path
from api import models
from api import process


async def create_archive(name):
    """
    Create the archive with the given name, including the template files to configure
    the archive.
    """
    path = os.getenv('ARCHIVE_FILES') + '/' + name

    # create the archive
    cmds = [['svnadmin', 'create', path]]

    # copy the current archive template files into the new archive filesystem
    result = await process.run_command(*['ls', '/var/api/svntemplate'])
    filenames = result['output'].strip().split('\n')
    cmds += [['cp', '-R', f'/var/api/svntemplate/{fn}', path] for fn in filenames]
    cmds += [['chown', '-R', 'apache:apache', path]]

    result = {'output': '', 'error': ''}
    for cmd in cmds:
        r = await process.run_command(*cmd)
        result['output'] += r['output']
        result['error'] += r['error']
        if result['error']:
            break

    return result


async def info(*urls, rev='HEAD'):
    """
    Return a list of Info data on the given url(s) and revision.
    """
    # `svn info` cannot take a revision range
    if ':' in rev:
        result = {'error': f"Revision range not allowed: rev={rev}"}
    else:
        cmd = ['svn', 'info', '--revision', rev, '--xml'] + list(urls)
        result = await process.run_command(*cmd)
        if not result['error']:
            xml = etree.fromstring(result.pop('output').encode())
            result['data'] = [
                models.Info.from_info(entry, rev=rev).dict()
                for entry in xml.xpath('/info/entry')
            ]
    return result


async def list_files(url, rev='HEAD'):
    """
    Return a list of files at the given url and revision as Info data. The url must be a
    directory.
    """
    # `svn list` cannot take a revision range
    if ':' in rev:
        result = {'error': f'Revision range not allowed: rev={rev}'}
    else:
        cmd = ['svn', 'list', '--revision', rev, '--xml', url]
        result = await process.run_command(*cmd)
        if not result['error']:
            xml = etree.fromstring(result.pop('output').encode())
            result['data'] = [
                models.Info.from_list(entry, rev=rev).dict()
                for entry in xml.xpath(f'/lists/list[@path="{url}"]/entry')
            ]
    return result


async def log(url, rev='HEAD'):
    """
    Return a list of LogEntry data on the given url and revision (single or range).
    """
    cmd = ['svn', 'log', '--revision', str(rev), '--xml', '--verbose', url]
    result = await process.run_command(*cmd)
    if not result['error']:
        xml = etree.fromstring(result.pop('output').encode())
        result['data'] = [
            models.LogEntry.from_logentry(entry).dict()
            for entry in xml.xpath('/log/logentry')
        ]
    return result


async def props(url, rev='HEAD'):
    """
    Return a dict with the props data on the given url and revision.
    """
    # `svn proplist` cannot take a revision range
    if ':' in rev:
        result = {'error': f'Revision range not allowed: rev={rev}'}
    else:
        cmd = ['svn', 'proplist', '--revision', rev, '--xml', '--verbose', url]
        result = await process.run_command(*cmd)
        if not result['error']:
            xml = etree.fromstring(result.pop('output').encode())
            result['data'] = {
                property.get('name'): property.text
                for property in xml.xpath(f'/properties/target[@path="{url}"]/property')
            }
    return result


async def revprops(url, rev='HEAD'):
    """
    Return a dict with the revprops data on the given url and rev.
    """
    cmd = ['svn', 'proplist', '--revprop', '--revision', rev, '--xml', '--verbose', url]
    result = await process.run_command(*cmd)
    if not result['error']:
        xml = etree.fromstring(result.pop('output').encode())
        result['data'] = {
            property.get('name'): property.text
            for property in xml.xpath('/properties/revprops/property')
        }
    return result


async def propset(url, data):
    """
    Update props and revprops with the given url and data. Return a dict with the
    combined `output` and `error` from the commands run on the data.

    * If data['rev'] is given, edit revprops in an existing revision.

      * data['rev'] must not be a range (no ':' allowed)
      * data['props'] and data['propdel'] must not be present (cannot set or delete
        props on an existing revision)
      * data['revprops'] is a dict with revprops to set on the revision. For each
        revprop, call `svn propset --revprop`
      * data['revpropdel'] is a list with revprop keys to delete. For each key, call
        `svn propdel --revprop`

    * If data['rev'] is not given, call `svn mucc` to create a new revision with the
      given props and revprops.

      * data['props'] is a dict with props to set. For each prop, add a `propset`
        command to the `svn mucc` call
      * data['propdel'] is a list with prop keys to delete. For each key, add a
        `propdel` command to the `svn mucc` call
      * data['revprops'] is a dict with revprops to set on the revision. For each
        revprop key and value, add `--with-revprop key=value` to the `svn mucc` call
      * data['revpropdel'] must not be present (cannot delete revprops on a new
        revision)
    """
    rev = str(data.get('rev', ''))
    message = str(data.get('message', ''))

    if ':' in rev:
        result = {'error': f'Cannot set props on a revision range: rev={rev}'}

    elif rev:
        if data.get('props') or data.get('propdel'):
            result = {
                'error': f'Cannot set or delete props on an existing revision: rev={rev}'
            }
        else:
            result = {'error': '', 'output': ''}
            # set revprops on existing revision
            for key, val in data.get('revprops', {}).items():
                cmd = ['svn', 'propset', key, '--revprop', '-r', rev, str(val), url]
                res = await process.run_command(*cmd)
                result['error'] += res['error']
                result['output'] += res['output']

            # del revprops on existing revision
            for key in data.get('revpropdel', []):
                cmd = ['svn', 'propdel', key, '--revprop', '-r', rev, url]
                res = await process.run_command(*cmd)
                result['error'] += res['error']
                result['output'] += res['output']

    else:
        if data.get('revpropdel'):
            result = {'error': 'Cannot delete revprops without a revision'}

        elif data.get('props') or data.get('propdel'):
            if data.get('props'):
                message += (
                    "\npropset keys=['" + "', '".join(data['props'].keys()) + "']"
                )
            if data.get('propdel'):
                message += "\npropdel keys=['" + "', '".join(data['propdel']) + "']"

            # set/del props with new revision, optionally with revprops as well
            cmd = ['svnmucc', '-m', message.strip()]

            for key, val in data.get('revprops', {}).items():
                cmd += ['--with-revprop', f'{key}={val}']

            for key, val in data.get('props', {}).items():
                cmd += ['propset', key, str(val), url]

            for key in data.get('propdel', []):
                cmd += ['propdel', key, url]

            print(cmd)
            result = await process.run_command(*cmd)

        elif data.get('revprops'):
            # can't set revprops in the absence of editing/deleting props in a revision
            result = {
                'error': (
                    'Cannot set revprops without an existing revision or creating '
                    + 'a revision to set/delete props'
                )
            }

        else:
            result = {'error': '', 'output': 'No change'}

    return result


async def put(url, body=None, message=None, revprops=None):
    """
    Create or update a file or directory at the given url with the given body (if any),
    message (if any), and revprops (if any) for the new revision. Return a process
    result object containing `output` and `error` (if any) from the process.

    TODO: Allow setting props on the URL at the same time.

    Rules:

    * If body is given (not None or empty), we assume that a file is intended.
      * If the file doesn't exist, it will be created.
      * If the file exists, it will be updated.
      * If the given url is currently a folder, it and all its content will be
        overwritten by the new file. (TODO? Check for the existence of an existing
        folder and prevent this behavior?)
      * If the parent directory of the file doesn't exist, an error will be returned and
        the file will not be created. (TODO? Check for the existence of the parent
        folder and create it if it doesn't exist?)

    * If body is None or empty, we assume that a directory is intended.
      * If the directory doesn't exist, it will be created, along with all parents.
      * If the directory already exists, an error is returned (updating is meaningless).

    (NOTE: These rules closely mirror the default behaviors of the `svn` and `svn mucc`
    commands. Making the TODO? adjustments would require querying the archive before the
    put operations.)
    """
    message = message or 'PUT ' + re.sub(f"^{os.getenv('ARCHIVE_SERVER')}", "", url)

    if not body:
        # directory
        cmd = ['svn', 'mkdir', '--parents', '--message', message]
        for key, val in revprops.items():
            cmd += ['--with-revprop', f"{key}={val}"]

        cmd += [url]
        result = await process.run_command(*cmd)

    else:
        # file from body
        cmd = ['svnmucc', '--message', message]
        for key, val in revprops.items():
            cmd += ['--with-revprop', f"{key}={val}"]

        with tempfile.NamedTemporaryFile() as tf:
            tf.write(body)
            tf.seek(0)
            cmd += ['put', tf.name, url]
            result = await process.run_command(*cmd)

    return result


async def delete_archive(name):
    """
    Delete the named archive from the archive filesystem.

    (This is a hard filesystem delete of the entire archive and its history, which
    cannot be undone.)
    """
    path = Path(os.getenv('ARCHIVE_FILES')) / name
    if not os.path.exists(path):
        result = {'error': f'Archive not found: {name}'}
    else:
        cmd = ['rm', '-rf', str(path)]
        result = await process.run_command(*cmd)

    return result


async def remove(url, message=None, revprops=None):
    """
    Remove the given `url` and all its children (if a directory). Set the given
    `message` and `revprops` on the new revision.
    """
    message = message or 'DELETE ' + re.sub(f"^{os.getenv('ARCHIVE_SERVER')}", "", url)
    cmd = ['svn', 'rm', '--message', message]
    for key, val in revprops.items():
        cmd += ['--with-revprop', f"{key}={val}"]
    cmd += [url]
    result = await process.run_command(*cmd)
    result['error'] = re.sub(
        f"'{os.getenv('ARCHIVE_SERVER')}",
        f"'{os.getenv('ARCHIVE_URL')}",
        result['error'],
    )

    return result