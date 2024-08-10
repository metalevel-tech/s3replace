"""
Find and replace for an S3 bucket.

Usage:
  s3replace <bucket> [--dry-run] [--force-replace] [--access-key-id=<key>] [--secret-access-key=<key>]
  s3replace -h | --help
  s3replace --version

Options:
  -h --help                 Show this screen.
  --version                 Show version.
  --dry-run                 Don't replace.
  --access-key-id=<key>     AWS access key ID
  --secret-access-key=<key> AWS secret access key
  --force-replace           Don't ask for confirmation before replacing.
"""

from io import BytesIO
from os import makedirs, path
import re
import sys
import datetime
import logging
from boto3.session import Session
from docopt import docopt

# Search section
#
#                       '^aserving/4/(0|1)/.*\.html?' >> 0 - tests, 1 - prod tree
key_pattern = re.compile(r'^aserving/4/0/.*\.html?', flags=re.IGNORECASE)
search_barrier = re.compile(r'\<title\>TradeLG\<\/title\>', flags=re.IGNORECASE | re.MULTILINE)
needle_pattern_list = [
  # (needle_pattern, replace_with)
    (re.compile( r'href="(?:https?://|//)advercenter.com/?"', flags=re.IGNORECASE | re.MULTILINE ), 'href="https://www.tradelg.net"'),
    (re.compile( r'href="(?:https?://|//)advercenter.com/terms.*?"', flags=re.IGNORECASE | re.MULTILINE ), 'href="https://www.tradelg.net/terms-and-conditions"'),
    (re.compile( r'href="(?:https?://|//)advercenter.com/privacy.*?"', flags=re.IGNORECASE | re.MULTILINE ), 'href="https://www.tradelg.net/privacy-policy"'),
    (re.compile( r'href="(?:https?://|//)advercenter.com/contact.*?"', flags=re.IGNORECASE | re.MULTILINE ), 'href="https://www.tradelg.net/contact-us"'),
    (re.compile( r'href="(?:https?://|//)advercenter.com/files/tradelgterms.*?"', flags=re.IGNORECASE | re.MULTILINE ), 'href="https://www.tradelg.net/terms-and-conditions"'),
    (re.compile( r'href="(?:https?://|//)advercenter.com/files/tradelgprivacypolicy.*?"', flags=re.IGNORECASE | re.MULTILINE ), 'href="https://www.tradelg.net/privacy-policy"'),
    (re.compile( r'href="(?:https?://|//)advercenter.com/files/tradelgcontact.*?"', flags=re.IGNORECASE | re.MULTILINE ), 'href="https://www.tradelg.net/contact-us"'),
]
needle_pattern_max_count = 10

# Setup logging
log_dir = './logs'
log_file_name = path.join(log_dir, datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S.log'))
if not path.exists(log_dir):
    makedirs(log_dir)
fh = logging.FileHandler(log_file_name)
fh.setLevel(logging.DEBUG)
logging.basicConfig(
    level=logging.INFO,
    format="",
    # format="%(asctime)s - %(levelname)s - \n%(message)s",
    handlers=[fh]
)
processed_objects_counter = 0


def confirm(prompt):
    answer = ''
    while answer.lower() not in ['y', 'n']:
        answer = input('%s [Y/N]  ' % prompt).lower()
    return answer.lower() == 'y'


def save_backup(key_name, content, backup_dir='backups'):
    local_key_name = path.join(backup_dir, key_name)

    if path.exists(local_key_name):
        sys.stdout.write('💾  Skipping backup for "%s"\n\n' % key_name)
        return

    makedirs(path.dirname(local_key_name), exist_ok=True)

    with open(local_key_name, 'w') as backup_file:
        backup_file.write(content)

    sys.stdout.write('💾  Backup created for "%s"\n\n' % key_name)


def replace_object_content(key_object, new_content):
    existing_obj = key_object.get()
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

    key_object.put(**new_obj_kwargs)

def check_key_object(key_object, dont_replace=False, force_replace=False):
    content = BytesIO()
    object = key_object.Object()
    object.download_fileobj(content)

    try:
        html = content.getvalue().decode('UTF-8')
    except UnicodeDecodeError as err:
        sys.stdout.write('Error reading "%s": %s' % (
            key_object.key,
            str(err),
        ))
        return False

    if not search_barrier.search(html):
        sys.stdout.write('\x1b[2K')
        sys.stdout.write('\r⏩  Skipping "%s"' % key_object.key)
        return False

    global processed_objects_counter
    processed_objects_counter += 1

    sys.stdout.write('\x1b[2K')
    sys.stdout.write('\n%s\n' % ('-' * 50))
    sys.stdout.write('🌟  Check object [%s]: %s\n\n' % (processed_objects_counter, key_object.key))
    logging.info(f'\n{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\nCheck object [{processed_objects_counter}]: {key_object.key}')

    counter = 0
    new_html = html
    do_replace = False

    for needle_pattern, replace_with in needle_pattern_list:
        to_replace = needle_pattern.findall(new_html)

        if len(to_replace) > needle_pattern_max_count:
            # raise ValueError('More than %s match found in %s. Aborting.' % (needle_pattern_max_count, key_object.key))
            sys.stdout.write('\x1b[2K')
            sys.stdout.write('\n%s\n' % ('-' * 50))
            sys.stdout.write('🌵  More than %s match found for %s. Skipping.\n' % (needle_pattern_max_count, to_replace))
            save_backup(key_object.key, html, backup_dir='backups/too_many_matches')
            continue
        else:
            if to_replace and len(to_replace) > 0 and to_replace[0]:
                do_replace = True
                new_html = needle_pattern.sub(replace_with, new_html, count=needle_pattern_max_count)
                counter += 1
                sys.stdout.write('[%d] %s\n    %s\n' % (counter, to_replace[0], replace_with))
                logging.info('[%d] %s\n    %s' % (counter, to_replace[0], replace_with))


    if do_replace is True:
        if dont_replace is True:
            return True
        if force_replace is True:
            sys.stdout.write('🚀  Replacing in "%s"\n' % key_object.key)
            save_backup(key_object.key, html)
            replace_object_content(key_object, new_html)
            logging.info(f'Replace object: {key_object.key}')
            return True
        if confirm('❓  Replace snippet in "%s"?' % key_object.key):
            sys.stdout.write('✅  Replacing in "%s"\n' % key_object.key)
            save_backup(key_object.key, html)
            replace_object_content(key_object, new_html)
            logging.info(f'Replace object: {key_object.key}')
        else:
            sys.stdout.write('❌  Skipping in "%s"\n' % key_object.key)
            logging.info(f'Replace object skipped: {key_object.key}')

    sys.stdout.write('\n')
    return True

def search_bucket(bucket, dont_replace=False, force_replace=False):
    try:
        sys.stdout.write('\n🍵  Searching AWS bucket "%s"\n\n' % bucket.name)
        logging.info(f'\n{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n> Search bucket: {bucket.name}\n> Replace: {not dont_replace}\n> Dry run: {dont_replace}\n> Force: {force_replace}\n> Key pattern: {key_pattern.pattern}\n> Search barrier: {search_barrier.pattern}')

        for key in bucket.objects.all():
            if key_pattern.match(key.key):
                sys.stdout.write('\x1b[2K') # Erase previous line
                sys.stdout.write('\r🔍  Checking "%s"' % key.key) # Write the searched key name
                check_key_object(key, dont_replace=dont_replace, force_replace=force_replace)
            else:
                sys.stdout.write('\x1b[2K')
                sys.stdout.write('\r⏩  Skipping "%s"' % key.key)

    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    args = docopt(__doc__, version='0.0.2')

    session = Session(
        aws_access_key_id=args['--access-key-id'],
        aws_secret_access_key=args['--secret-access-key'],
    )

    s3 = session.resource('s3')
    bucket = s3.Bucket(args['<bucket>'])

    search_bucket(bucket, dont_replace=args['--dry-run'], force_replace=args['--force-replace'])
