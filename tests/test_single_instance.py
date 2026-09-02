"""
Tests for single_instance.py — added 2026-08-26 alongside the fix for 6
duplicate main.py-related processes running at once (see
bugs_duplicate_bot_instances memory). Uses real file handles against a
tmp_path lock file rather than touching the real main.lock, so it never
interferes with an actual running bot.
"""
import os

import pytest

import single_instance


def test_acquire_returns_handle_and_writes_own_pid(tmp_path):
    lock_path = tmp_path / "test.lock"
    fh = single_instance.acquire(lock_path)
    try:
        fh.seek(0)
        assert fh.read().strip() == str(os.getpid())
    finally:
        single_instance.release(fh)


def test_second_acquire_raises_while_first_holds_lock(tmp_path):
    lock_path = tmp_path / "test.lock"
    fh1 = single_instance.acquire(lock_path)
    try:
        with pytest.raises(single_instance.AlreadyRunningError):
            single_instance.acquire(lock_path)
    finally:
        single_instance.release(fh1)


def test_acquire_succeeds_again_after_release(tmp_path):
    lock_path = tmp_path / "test.lock"
    fh1 = single_instance.acquire(lock_path)
    single_instance.release(fh1)

    fh2 = single_instance.acquire(lock_path)  # would raise if the lock leaked
    single_instance.release(fh2)


def test_release_is_a_noop_on_none():
    single_instance.release(None)  # must not raise
