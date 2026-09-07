"""Compile and run the C host test for the sketch's HTTP response handling.

The sketch's accept/reject logic lives in sketch/ardtemp-r4wifi/response.h so it
can be compiled on the host. response_parse_test.c includes that header
directly, so these assertions cover the code that runs on the board.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
SOURCE = TESTS_DIR / 'response_parse_test.c'
HEADER = TESTS_DIR.parent / 'sketch' / 'ardtemp-r4wifi' / 'response.h'

# CI sets this so a missing compiler fails the build instead of quietly
# skipping — a skipped test still shows up as a green check.
REQUIRED = os.environ.get('ARDTEMP_REQUIRE_C_TESTS') == '1'


def _compiler():
    for name in ('cc', 'gcc', 'clang'):
        found = shutil.which(name)
        if found:
            return found
    if REQUIRED:
        pytest.fail(
            'No C compiler found, but ARDTEMP_REQUIRE_C_TESTS=1. '
            'Install gcc or clang.'
        )
    pytest.skip('no C compiler available to build the sketch host test')


def test_sources_present():
    assert HEADER.is_file(), f'missing sketch header: {HEADER}'
    assert SOURCE.is_file(), f'missing host test: {SOURCE}'


def test_response_classification(tmp_path):
    binary = tmp_path / 'response_parse_test'
    build = subprocess.run(
        [_compiler(), '-O2', '-Wall', '-Wextra', '-Werror',
         '-o', str(binary), str(SOURCE)],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, (
        f'host test failed to compile:\n{build.stderr}'
    )

    run = subprocess.run([str(binary)], capture_output=True, text=True)
    sys.stdout.write(run.stdout)
    assert run.returncode == 0, (
        f'response classification cases failed:\n{run.stdout}{run.stderr}'
    )
    assert 'passed' in run.stdout
