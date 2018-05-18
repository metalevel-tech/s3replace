"""
Find and replace for an S3 bucket.

Usage:
  s3replace <bucket> [--dry-run] [--access-key-id=<key>] [--secret-access-key=<key>]
  s3replace -h | --help
  s3replace --version

Options:
  -h --help                 Show this screen.
  --version                 Show version.
  --dry-run                 Don't replace.
  --access-key-id=<key>     AWS access key ID
  --secret-access-key=<key> AWS secret access key
"""
from io import BytesIO
from os import makedirs, path
import re
import sys

from boto3.session import Session
from docopt import docopt


key_pattern = re.compile(r'.*\.html', flags=re.IGNORECASE)
needle_pattern = re.compile(
    r'<script .*[\n\r]{0,2}.*https?://s?res\.dallasnews\.com/reg/js/tp_jsinclude\.js.*[\n\r]{0,2}\s*</script>',  # noqa
    flags=re.IGNORECASE | re.MULTILINE
)
replace_with = '<script type="text/javascript" src="//interactives.dallasnews.com/common/templates/v1.1/js/meter.js"></script>'  # noqa


def confirm(prompt):
    answer = ''
    while answer.lower() not in ['y', 'n']:
        answer = input('%s [Y/N]  ' % prompt).lower()
    return answer.lower() == 'y'


def check_key(object_summary):
    content = BytesIO()
    object = object_summary.Object()
    object.download_fileobj(content)

    try:
        html = content.getvalue().decode('UTF-8')
    except UnicodeDecodeError as err:
        sys.stdout.write('Error reading "%s": %s' % (
            object_summary.key,
            str(err),
        ))
        return False, None, None

    to_replace = needle_pattern.findall(html)

    if len(to_replace) > 1:
        raise ValueError(
            'More than one match found in %s. Aborting.' % object_summary.key
        )

    if to_replace:
        return True, to_replace[0], html
    return False, None, None


def save_backup(key_name, content, backup_dir='backups'):
    local_key_name = path.join(backup_dir, key_name)
    makedirs(path.dirname(local_key_name), exist_ok=True)

    with open(local_key_name, 'w') as backup_file:
        backup_file.write(content)


def replace_key_content(object_summary, new_content):
    existing_obj = object_summary.get()
    new_obj_kwargs = dict(
        ACL='public-read',
        Body=new_content.encode('UTF-8')
    )

    for _ in ('CacheControl', 'ContentDisposition', 'ContentEncoding',
              'ContentType', 'WebsiteRedirectLocation',):
        if _ in existing_obj and existing_obj[_]:
            new_obj_kwargs[_] = existing_obj[_]

    if existing_obj['Metadata']:
        new_obj_kwargs['Metadata'] = existing_obj['Metadata']

    object_summary.put(**new_obj_kwargs)


def search_bucket(bucket, dont_replace=False):
    sys.stdout.write('\nSearching AWS bucket "%s"\n\n' % bucket.name)

    for key in bucket.objects.all():
        if key_pattern.match(key.key):
            sys.stdout.write('\x1b[2K')
            sys.stdout.write('\r🔍  Checking "%s"' % key.key)

            matched, match_content, html = check_key(key)

            if matched:
                sys.stdout.write('\x1b[2K')
                sys.stdout.write('\n\n%s\n' % ('-' * 125))
                sys.stdout.write(match_content)
                sys.stdout.write('\n%s\n\n' % ('-' * 125))

                if dont_replace is True:
                    sys.stdout.write('Match found in "%s".\n' % key.key)
                    continue

                if confirm('Replace snippet in "%s"?' % key.key):
                    sys.stdout.write('✅  Replacing in "%s"\n' % key.key)
                    save_backup(key.key, html)
                    replace_key_content(
                        key,
                        needle_pattern.sub(replace_with, html)
                    )
                else:
                    sys.stdout.write('❌  Skipping in "%s"\n' % key.key)
        else:
            sys.stdout.write('\x1b[2K')
            sys.stdout.write('\r⏩  Skipping "%s"' % key.key)


if __name__ == '__main__':
    args = docopt(__doc__, version='0.0.1')

    session = Session(
        aws_access_key_id=args['--access-key-id'],
        aws_secret_access_key=args['--secret-access-key'],
    )
    s3 = session.resource('s3')
    bucket = s3.Bucket(args['<bucket>'])

    search_bucket(bucket, dont_replace=args['--dry-run'])
