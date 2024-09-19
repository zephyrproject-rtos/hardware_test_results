#!/bin/env python3
# Copyright (c) 2024 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import sys
import tempfile
from argparse import ArgumentParser
from dataclasses import dataclass, field
from pathlib import Path

import iniconfig
from git import Repo, Actor, InvalidGitRepositoryError, Remote
from github import Github, Auth, GithubException

INI_CONFIG: str = 'uploader.ini'
INI_SECTION_NAME: str = 'uploader'


class PullRequestManagerException(Exception):
    """General exception for PullRequestManager."""


class ConfigurationError(PullRequestManagerException):
    """Wrong configuration."""


@dataclass
class Config:
    forked_repo: str
    upstream_repo: str
    author_name: str | None = None
    author_email: str | None = None
    commit_message: str = 'Added a new files'
    branch_name_pattern: str = 'results-%Y%m%d%H%M%S'
    upstream_repo_name: str = field(init=False, repr=False)
    forked_repo_name: str = field(init=False, repr=False)
    target_branch_name: str = field(init=False, repr=False, default='main')

    def __post_init__(self):
        self._now = datetime.datetime.now()
        self.upstream_repo_name = (
            self.upstream_repo
            .replace('https://github.com/', '')
            .replace('git@github.com:', '')
            .replace('.git', '')
        )
        self.forked_repo_name = (
            self.forked_repo
            .replace('https://github.com/', '')
            .replace('git@github.com:', '')
            .replace('.git', '')
        )

    @property
    def local_branch_name(self) -> str:
        return self._now.strftime(self.branch_name_pattern)

    @property
    def local_repo_dir(self) -> Path:
        return Path(f'{tempfile.gettempdir()}/uploader/{self._now.strftime("%Y%m%d%H%M%S")}')


class PullRequestManager:

    def __init__(self, repo_directory: Path, config: Config) -> None:
        self.config = config
        self.local_repo_dir: Path = repo_directory or config.local_repo_dir
        self.pr_description: str = ''  # description for Pull Request

    def commit_files(self, files: list[Path]) -> None:
        """
        Commit files to the target branch.

        :param files: list of files to commit
        """
        # clone a forked repository
        forked_repo = self._clone_forked_repository()

        # find upstream repo or add it if not added yet
        upstream = self._add_upstream_repository(forked_repo)
        upstream.fetch()

        # check if local repository is clean
        if forked_repo.is_dirty(untracked_files=False):
            print('Local repo is not clean')
            sys.exit(1)

        # create new branch locally
        logging.info('Checkout new branch %s', self.config.local_branch_name)
        new_branch = forked_repo.create_head(self.config.local_branch_name, upstream.refs[self.config.target_branch_name])
        new_branch.checkout()

        forked_repo.create_remote(self.config.local_branch_name, self.config.forked_repo)

        # copy files
        new_files = self._copy_files(files)
        self._commit_files(forked_repo, new_files)

        logging.info('Pushing to origin')
        forked_repo.git.push('origin', self.config.local_branch_name)

    def _commit_files(self, repo: Repo, files: list[Path]) -> None:
        # add new files to repo
        logging.info('Adding new files to the index')
        repo.index.add(files)  # Add it to the index.
        # Commit the changes to deviate masters history
        if self.config.author_name and self.config.author_email:
            author = Actor(self.config.author_name, self.config.author_email)
        else:
            author = None
        commit = repo.index.commit(self.config.commit_message, author=author)
        logging.info('Added commit: %s', commit.summary)

    def _add_upstream_repository(self, forked_repo: Repo) -> Remote:
        for remote in forked_repo.remotes:
            if remote.name == 'upstream':
                upstream = remote
                logging.info('Found remote upstream repository %s in %s', remote.url, self.local_repo_dir)
                if upstream.url != self.config.upstream_repo:
                    raise ConfigurationError(
                        f'Upstream repository is already added '
                        f'but it not match the one from configuration: {self.config.upstream_repo}'
                    )
                return upstream
        # if upstream remote is not added let's add it
        logging.info('Adding upstream remote repository %s', self.config.upstream_repo)
        upstream = forked_repo.create_remote('upstream', self.config.upstream_repo)
        return upstream

    def _clone_forked_repository(self) -> Repo:
        if self.local_repo_dir.exists() and not self.local_repo_dir.is_dir():
            print(f'It is not a directory: {self.local_repo_dir}', file=sys.stderr)
            sys.exit(1)
        if self.local_repo_dir.exists():
            try:
                forked_repo = Repo(self.local_repo_dir)
            except InvalidGitRepositoryError:
                print(f'It is not valid git repository: {self.local_repo_dir}', file=sys.stderr)
                sys.exit(1)
        else:
            self.local_repo_dir.mkdir(exist_ok=True, parents=True)
            logging.info('Cloning repository %s to %s', self.config.forked_repo, self.local_repo_dir)
            forked_repo = Repo.clone_from(self.config.forked_repo, self.local_repo_dir, single_branch=True)
        return forked_repo

    def _copy_files(self, files: list[Path]) -> list[Path]:
        new_files = []
        for file in files:
            zephyr_version = self._get_zephyr_version(file)
            if zephyr_version is None:
                continue
            new_directory = self.local_repo_dir / 'results' / zephyr_version
            new_directory.mkdir(exist_ok=True, parents=True)
            dest_file = new_directory / file.name
            logging.info('Copy file %s to %s', file, dest_file)
            shutil.copyfile(file, dest_file)
            new_files.append(dest_file)
            self.pr_description += f'- {zephyr_version}\n'
        return new_files

    @staticmethod
    def _get_zephyr_version(file: Path) -> str | None:
        with open(file, 'r') as f:
            data = json.load(f)
        try:
            zephyr_version = data['environment']['zephyr_version']
            logging.info('Found zephyr version: %s', zephyr_version)
            return zephyr_version
        except KeyError:
            logging.error('Zephyr version not found in file %s', file)
            return None

    def create_pull_request(self, title: str, description: str, token: str) -> str:
        """
        Create pull request to upstream repository.

        :param title: Pull request title
        :param description: Pull request description
        :param token: github token
        :return: URL to created pull request
        """
        auth = Auth.Token(token)
        github = Github(auth=auth)

        upstream_repo = github.get_repo(self.config.upstream_repo_name)
        forked_repo = github.get_repo(self.config.forked_repo_name)
        if self.config.local_branch_name not in [b.name for b in forked_repo.get_branches()]:
            raise RuntimeError(f'Branch {self.config.local_branch_name} does not exist')

        # create pull request
        logging.info(
            'Creating pull request from %s to %s', self.config.forked_repo, self.config.upstream_repo
        )
        user_name = forked_repo.full_name.replace(f'/{forked_repo.name}', '')
        if self.pr_description:
            description += f'\n{self.pr_description}'
        try:
            pull_request = upstream_repo.create_pull(
                title=title,
                body=description,
                head=f'{user_name}:{self.config.local_branch_name}',
                base=self.config.target_branch_name
            )
        except GithubException as exc:
            logging.error('Failed to create pull request: %s', exc.message)
            raise
        logging.info(f'Pull request created: {pull_request.html_url}')

        github.close()
        return pull_request.html_url


def load_configuration_from_file(ini_file) -> Config:
    try:
        return _load_configuration_from_file(ini_file)
    except iniconfig.exceptions.ParseError:
        print(f'Invalid configuration file: {ini_file}', file=sys.stderr)
        sys.exit(1)


def _load_configuration_from_file(ini_file) -> Config:
    ini_config = iniconfig.IniConfig(ini_file)
    config_dict = {}

    # mandatory options
    mandatory_options = [
        'forked_repo', 'upstream_repo'
    ]
    for option in mandatory_options:
        if value := ini_config.get(INI_SECTION_NAME, option):
            config_dict[option] = value
        else:
            print(f'Option "{option}" must be provided in {ini_file}', file=sys.stderr)
            sys.exit(1)

    # optional options
    optional_options = [
        'author_name',
        'author_email',
        'commit_message',
        'branch_name_pattern'
    ]
    for option in optional_options:
        if value := ini_config.get(INI_SECTION_NAME, option):
            config_dict[option] = value

    return Config(**config_dict)


def main():
    logging.basicConfig(level=logging.INFO)

    parser = ArgumentParser(description='Script for uploading files to a remote repository.')
    parser.add_argument('--token', required=True, help='github token')
    parser.add_argument('--results-directory', '-r', required=True, type=Path, metavar='PATH',
                        help='directory where results files are stored')
    parser.add_argument('--pattern', '-p', required=False, type=str, default='*.json',
                        help='shell-type wildcards pattern for results files, e.g. "*.json" (default: %(default)s)')
    parser.add_argument('--title', '-t', required=True, help='commit title')
    parser.add_argument('--body', '-b', required=False, default='', help='commit description')
    parser.add_argument('--repo-directory', '-d', required=False, type=Path, metavar='PATH',
                        help='directory where forked repository is stored, if not provided it will be cloned to tmp')
    parser.add_argument('--config', '-c', required=False, default=INI_CONFIG, type=Path, metavar='PATH',
                        help='path to configuration file (default: %(default)s)')
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f'Cannot find configuration file: {args.config}', file=sys.stderr)
        return 1

    config = load_configuration_from_file(args.config)
    token = args.token
    title = args.title
    description = args.body
    directory = args.results_directory
    pattern = args.pattern
    forked_repo_directory = args.repo_directory

    files = [f for f in directory.glob(pattern)]

    if not files:
        print('No files to commit', file=sys.stderr)
        return 1
    if len(token) == 0:
        print('Github token cannot be empty', file=sys.stderr)
        return 1

    pr_manager = PullRequestManager(forked_repo_directory, config)
    try:
        pr_manager.commit_files(files)
        url = pr_manager.create_pull_request(title=title, description=description, token=token)
    except PullRequestManagerException as exc:
        logging.error(exc)
        return 1

    print(f'Created pull request: {url}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
