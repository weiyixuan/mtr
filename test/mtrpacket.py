#
#   mtr  --  a network diagnostic tool
#   Copyright (C) 2016  Matt Kimball
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License version 2 as
#   published by the Free Software Foundation.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
#

'''Infrastructure for running tests which invoke mtr-packet.'''

import fcntl
import os
import select
import subprocess
import sys
import time
import unittest


class ReadReplyTimeout(Exception):
    'Exception raised by TestProbe.read_reply upon timeout'

    pass


class WriteCommandTimeout(Exception):
    'Exception raised by TestProbe.write_command upon timeout'

    pass


def set_nonblocking(file_descriptor):  # type: (int) -> None
    'Put a file descriptor into non-blocking mode'

    flags = fcntl.fcntl(file_descriptor, fcntl.F_GETFL)

    # pylint: disable=locally-disabled, no-member
    fcntl.fcntl(file_descriptor, fcntl.F_SETFL, flags | os.O_NONBLOCK)


class MtrPacketTest(unittest.TestCase):
    '''Base class for tests invoking mtr-packet.

    Start a new mtr-packet subprocess for each test, and kill it
    at the conclusion of the test.

    Provide methods for writing commands and reading replies.
    '''

    def __init__(self, *args):
        self.reply_buffer = None  # type: unicode
        self.packet_process = None  # type: subprocess.Popen
        self.stdout_fd = None  # type: int

        super(MtrPacketTest, self).__init__(*args)

    def setUp(self):
        'Set up a test case by spawning a mtr-packet process'

        packet_path = os.environ.get('MTR_PACKET', './mtr-packet')

        self.reply_buffer = ''
        self.packet_process = subprocess.Popen(
            [packet_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE)

        #  Put the mtr-packet process's stdout in non-blocking mode
        #  so that we can read from it without a timeout when
        #  no reply is available.
        self.stdout_fd = self.packet_process.stdout.fileno()
        set_nonblocking(self.stdout_fd)

        self.stdin_fd = self.packet_process.stdin.fileno()
        set_nonblocking(self.stdin_fd)

    def tearDown(self):
        'After a test, kill the running mtr-packet instance'

        self.packet_process.stdin.close()
        self.packet_process.stdout.close()

        try:
            self.packet_process.kill()
        except OSError:
            return

    def read_reply(self, timeout=10.0):  # type: (float) -> unicode
        '''Read the next reply from mtr-packet.

        Attempt to read the next command reply from mtr-packet.  If no reply
        is available withing the timeout time, raise ReadReplyTimeout
        instead.'''

        start_time = time.time()

        #  Read from mtr-packet until either the timeout time has elapsed
        #  or we read a newline character, which indicates a finished
        #  reply.
        while True:
            now = time.time()
            elapsed = now - start_time

            select_time = timeout - elapsed
            if select_time < 0:
                select_time = 0

            select.select([self.stdout_fd], [], [], select_time)

            reply_bytes = None

            try:
                reply_bytes = os.read(self.stdout_fd, 1024)
            except OSError:
                pass

            if reply_bytes:
                self.reply_buffer += reply_bytes.decode('utf-8')

            #  If we have read a newline character, we can stop waiting
            #  for more input.
            newline_ix = self.reply_buffer.find('\n')
            if newline_ix != -1:
                break

            if elapsed >= timeout:
                raise ReadReplyTimeout()

        reply = self.reply_buffer[:newline_ix]
        self.reply_buffer = self.reply_buffer[newline_ix + 1:]
        return reply

    def write_command(self, cmd, timeout=10.0):
        # type: (unicode, float) -> None

        '''Send a command string to the mtr-packet instance, timing out
        if we are unable to write for an extended period of time.  The
        timeout is to avoid deadlocks with the child process where both
        the parent and the child are writing to their end of the pipe
        and expecting the other end to be reading.'''

        command_str = cmd + '\n'
        command_bytes = command_str.encode('utf-8')

        start_time = time.time()

        while True:
            now = time.time()
            elapsed = now - start_time

            select_time = timeout - elapsed
            if select_time < 0:
                select_time = 0

            select.select([], [self.stdin_fd], [], select_time)

            bytes_written = 0
            try:
                bytes_written = os.write(self.stdin_fd, command_bytes)
            except OSError:
                pass

            command_bytes = command_bytes[bytes_written:]
            if not len(command_bytes):
                break

            if elapsed >= timeout:
                raise WriteCommandTimeout()


def check_running_as_root():
    'Print a warning to stderr if we are not running as root.'

    # pylint: disable=locally-disabled, no-member
    if sys.platform != 'cygwin' and os.getuid() > 0:
        sys.stderr.write(
            "Warning: Many tests require running as root\n")
