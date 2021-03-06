#!/usr/bin/env python

from __future__ import print_function

import argparse
import os
import pkg_resources
import subprocess
import sys
import time

from threading import Timer

from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

version = pkg_resources.require('rundogd')[0].version


class Runner(object):
    def __init__(self, command, stdout=None, stderr=None):
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.outfp = None
        self.errfp = None
        self.process = None
        self.restart()

    def poll(self):
        if self.process:
            try:
                self.process.poll()
                return self.process.returncode
            except OSError:
                self.process = None
        return None

    def terminate(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait()
            except OSError:
                pass
            self.process = None

    def restart(self):
        self.terminate()

        if self.outfp:
            os.close(self.outfp)
        if self.errfp:
            os.close(self.errfp)

        print('$', ' '.join(self.command))
        try:
            if self.stdout:
                self.outfp = os.open(
                    self.stdout,
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                    0o644
                )
            if self.stderr:
                self.errfp = os.open(
                    self.stderr,
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                    0o644
                )

            self.process = subprocess.Popen(
                self.command,
                stdout=self.outfp,
                stderr=self.errfp
            )
        except OSError as e:
            print('Failed to start process:', e)
            sys.exit(1)


class ChangeHandler(PatternMatchingEventHandler):
    def __init__(self, runner, wait, verbosity, **kwargs):
        self.runner = runner
        self.wait = wait
        self.verbosity = verbosity
        self.timer = None

        PatternMatchingEventHandler.__init__(self, **kwargs)

    def on_any_event(self, event):
        if self.verbosity:
            if hasattr(event, 'src_path'):
                print('[RUNDOGDEBUG] event src_path:', event.src_path)
            if hasattr(event, 'dest_path'):
                print('[RUNDOGDEBUG] event dest_path:', event.dest_path)

        def restart():
            print('---------- Restarting... ----------')
            self.runner.restart()

        # Restart after wait period has passed
        # This ensures all file events have completed
        # by the time the command is restarted.
        if self.timer and self.timer.is_alive():
            self.timer.cancel()

        self.timer = Timer(self.wait, restart)
        self.timer.start()


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Filesystem watcher-restarter daemon.',
        usage='%(prog)s [options] command',
        prog='rundogd'
    )
    parser.add_argument(
        '-p', '--path',
        action='append',
        nargs=1,
        help='recursively watch for file changes in this path'
    )
    parser.add_argument(
        '-r', '--persist',
        action='store_true',
        help='continue watching files after the command exits'
    )
    parser.add_argument(
        '-e', '--exclude',
        action='append',
        nargs=1,
        help='exclude files matching the given pattern'
    )
    parser.add_argument(
        '-d', '--exclude-dir',
        action='store_true',
        help='exclude changes to directories'
    )
    parser.add_argument(
        '-o', '--only',
        action='append',
        nargs=1,
        help='only watch files matching the given pattern'
    )
    parser.add_argument(
        '-w', '--wait',
        type=float,
        default=0.5,
        help='a delay time (in seconds) to wait between file changes'
    )
    parser.add_argument(
        '--stdout',
        help='redirect stdout to this file'
    )
    parser.add_argument(
        '--stderr',
        help='redirect stderr to this file'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='count',
        help='enable verbose output; use more v\'s for more verbosity'
    )
    parser.add_argument(
        '--version',
        action='version',
        version='%(prog)s ' + version
    )
    parser.add_argument('command')
    parser.add_argument('args', nargs=argparse.REMAINDER)

    args = parser.parse_args()

    # Require a command
    if args.command is None:
        parser.error('Missing command.')

    # Get `path` arguments
    if args.path:
        paths = map(lambda x: x[0], args.path)
    else:
        # Or infer it from the command
        paths = [ os.path.expanduser(os.path.dirname(args.command)) ]

    # Validate `path` arguments
    # Replace empty path with current working directory
    for i, path in enumerate(paths):
        if not path:
            paths[i] = '.'

    # Remove duplicate paths
    paths = set(paths)

    # Ensure all paths are directories
    for path in paths:
        if not os.path.isdir(path):
            print(path, 'is not a directory.')
            sys.exit(1)

    # Get `exclude` arguments
    exclude = None
    if args.exclude:
        exclude = set(map(lambda x: x[0], args.exclude))
        if not exclude:
            exclude = None

    # Get `only` arguments
    only = None
    if args.only:
        only = set(map(lambda x: x[0], args.only))
        if not only:
            only = None

    # Get command argument, and start the process
    runner = Runner([ args.command ] + args.args, args.stdout, args.stderr)

    # Start the watchdog observer thread
    event_handler = ChangeHandler(
        runner,
        args.wait,
        args.verbose,
        patterns=only,
        ignore_patterns=exclude,
        ignore_directories=args.exclude_dir
    )
    observer = Observer()
    for path in paths:
        observer.schedule(event_handler, path=path, recursive=True)
    observer.start()

    # Enter Rip Van Winkle mode
    try:
        while True:
            time.sleep(1)
            if (not args.persist) and (runner.poll() is not None):
                break
    except KeyboardInterrupt:
        pass

    print('\nrundogd is shutting down...')
    runner.terminate()
    observer.stop()
    observer.join()
