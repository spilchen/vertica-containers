#!/usr/bin/env python
"""
python timing_loop.py 

Probably should drive this from a JSON file, but right now what I do
is define three lists of commands:

 - prologue --- list of commands to run to set things up
 - timed_commands --- list of commands to run in a loop, timing each
        command.  It is expected that you run each of these commands
        repeatedly, which suits my purpose, but may not be ideal for
        others
 - epilogue --- cleanup commands

Note that there is also a loop_count variable for how many times each 
command is to be executed. 

Concludes by printing (min, max, mean, command) of times for each command
"""

import argparse
import collections
import statistics
import time
import os
import vertica_python

CWD = os.getcwd()

class TimerError(Exception):
    """A custom exception used to report errors in use of Timer class"""

class Timer:
    def __init__(self):
        self._start_time = None
        self._elapsed_time = 0

    def start(self):
        """Start a new timer"""
        if self._start_time is not None:
            raise TimerError(f"Timer is running. Use .stop() to stop it")
        self._start_time = time.perf_counter()

    def stop(self):
        """Stop the timer, and report the elapsed time"""
        if self._start_time is None:
            raise TimerError(f"Timer is not running. Use .start() to start it")
        _elapsed_time = time.perf_counter() - self._start_time
        self._start_time = None
        return _elapsed_time

    def print(self, operation=None):
        if operation:
            print(f"{operation} took: {elapsed_time:0.4f} seconds")
        else:
            print(f"Elapsed time: {elapsed_time:0.4f} seconds")

conn_info = {'host': '127.0.0.1',
             'port': 7132,
             'user': 'dbadmin',
             # 'password': 'some_password',
             'database': 'vwasmsdk', 
             # autogenerated session label by default,
             # 'session_label': 'some_label',
             # default throw error on invalid UTF-8 results
             'unicode_error': 'strict',
             # SSL is disabled by default
             'ssl': False,
             # autocommit is off by default
             'autocommit': True,
             # using server-side prepared statements is disabled by default
             'use_prepared_statements': True,
             # connection timeout is not enabled by default
             # 5 seconds timeout for a socket operation (Establishing a TCP connection or read/write operation)
             # 'connection_timeout': 60
             }


cwasmlib = f"'{CWD}/build/cWasmUDx.so'"
nonwasmlib = f"'{CWD}/build/nonWasmUDx.so'"
rustwasmlib = f"'{CWD}/build/rustWasmUDx.so'"

prologue = [
    f"CREATE OR REPLACE LIBRARY cwasmudx AS {cwasmlib} LANGUAGE 'C++'",
    f"CREATE OR REPLACE LIBRARY nonwasmudx AS {nonwasmlib} LANGUAGE 'C++'",
    f"CREATE OR REPLACE LIBRARY rustwasmudx AS {rustwasmlib} LANGUAGE 'C++'",
    f"CREATE OR REPLACE FUNCTION nonWasmUDx_sumFactory AS LANGUAGE 'C++' NAME 'nonWasmUDx_sumFactory' LIBRARY nonwasmudx NOT FENCED",
    f"CREATE OR REPLACE FUNCTION rustWasmUDx_sumFactory AS LANGUAGE 'C++' NAME 'rustWasmUDx_sumFactory' LIBRARY rustwasmudx NOT FENCED",
    f"CREATE OR REPLACE FUNCTION cWasmUDx_sumFactory AS LANGUAGE 'C++' NAME 'cWasmUDx_sumFactory' LIBRARY cwasmudx NOT FENCED",
    f'DROP TABLE IF EXISTS ct4', 
    f'DROP TABLE IF EXISTS rt4',
    f'DROP TABLE IF EXISTS nt4',
    f'DROP TABLE IF EXISTS st4',
    f"select start_session_trace('wasm', 1, 10)",
]

Command = collections.namedtuple('Command', ['label', 'command', 'cleanup'])

timed_commands = [
    Command('cWasmUDx_sum 10M rows',
            f"CREATE TABLE ct4 AS SELECT cWasmUDx_sum(c0, c1) FROM t3",
            "DROP TABLE ct4 CASCADE"),            
    Command('rustWasmUDx_sum 10M rows',
            f"CREATE TABLE rt4 AS SELECT rustWasmUDx_sum(c0, c1) FROM t3",
            "DROP TABLE rt4 CASCADE"),
    Command('nonWasmUDx_sum 10M rows',
            f"CREATE TABLE nt4 AS SELECT nonWasmUDx_sum(c0, c1) FROM t3",
            "DROP TABLE nt4 CASCADE"),
    Command('select c0 + c1',
            f"CREATE TABLE st4 AS SELECT c0 + c1 FROM t3",
            "DROP TABLE st4 CASCADE"),
]

epilogue = [
    f'select stop_session_trace()',
]

loop_count = 30

def report(cmd, timings):
    s = ('|'
         + '| '.join([f"{min(timings):0.4f}",
                   f"{max(timings):0.4f}",
                   f"{statistics.median(timings):0.4f}",
                   f"{statistics.stdev(timings):0.4f}",
                   f"{statistics.mean(timings):0.4f}",
                   f"{cmd}"])
         + '|')
    print(s)

def is_select(cmd):
    return "select" in cmd.lower()

def select_one(cur):
    """
    Force synchronization with the server by sending a pretty vacuous
    command and retrieving the result.

    If we don't do this, aren't we just measuring the time it takes to
    *send* a command to the server, not the time it takes for the
    server to execute the command?
    """
    cur.execute("SELECT 1")
    cur.fetchall()

def main():
    timings = {}

    with vertica_python.connect(**conn_info) as conn:
        cur = conn.cursor()
        for cmd in prologue:
            try:
                cur.execute(cmd)
                select_one(cur)
            except vertica_python.errors.QueryError as e:
                print(f"{cmd} got error")
                print(f"{e}")

        # This looks ugly in output, but it works great with org-mode buffers
        print("| min |    max |    median | std |    mean |   command|")
        print("|-+-+-+-+-+-|")
        for cmd in timed_commands:
            timings[cmd.label] = []
            for loop in range(loop_count):
                t = Timer()
                t.start()
                try:
                    cur.execute(cmd.command)
                    if is_select(cmd.command):
                        # if the command has "select" in it, read all the
                        # output --- this forces us to wait for the server
                        # to complete its task, so that we measure the
                        # time the task takes.
                        cur.fetchall()
                    else:
                        # force synchronization with the server (see
                        # select_one explanatory comment)
                        select_one(cur)
                    timings[cmd.label].append(t.stop())
                except vertica_python.errors.QueryError as e:
                    print(f"test {cmd.command} got error")
                    print(f"{e}")
                try:
                    cur.execute(cmd.cleanup)
                except vertica_python.errors.QueryError as e:
                    print(f"cleanup {cmd.cleanup} got error")
                    print(f"{e}")
                    
            report(cmd.label, timings[cmd.label])
        for cmd in epilogue:
            try:
                cur.execute(cmd)
            except vertica_python.errors.QueryError as e:
                print(f"{cmd} got error")
                print(f"{e}")
            
if __name__ == '__main__':
    main()

    
