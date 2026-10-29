"""Run locally before first use; also resets an existing admin password."""
from getpass import getpass
import sys
import auth
import database
import workflow


def main():
    database.init_db()
    auth.init_auth_db()
    workflow.init_workflow_db()
    if '--ensure' in sys.argv and auth.has_admin():
        return
    print('SaveBite - local administrator setup')
    print('Use the same admin username to reset an existing administrator password.')
    username = input('Admin username (English, 3-50 characters): ').strip()
    name = input('Display name: ').strip()
    password = getpass('Password (12-128 characters, hidden): ')
    repeat = getpass('Repeat password: ')
    if password != repeat:
        raise ValueError('Passwords do not match.')
    auth.local_admin_setup(username, name, password)
    print('Administrator saved. Start SaveBite and sign in.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print('Setup failed:', error)
        raise SystemExit(1)
