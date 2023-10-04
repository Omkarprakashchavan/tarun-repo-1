#!/bin/env python3

# A tool for various operations against the GitHub repos.

# ===== GLCP-121291 ===================================
__help_remove_topics = '''
  repo-ops.py remove-topics --lower-priority
   # Repos with multiple GitHub topics (glcp-production, glcp-not-production, glcp-unknown)
   # will be updated and the lower priority topics will be removed.
   # "glcp-production" has the highest priority and "glcp-unknown" has the lowest priority
'''

# ===== GLCP-121289 ===================================
__help_email_report = '''
  repo-ops.py email-report --each-manager
   # Send email to each second line manager and attach the CSV file

  repo-ops.py email-report --unknown-repos
   # Send email to each developer and first line manager about each repo
   # that has only the "glcp-unknown" GitHub topic (and does NOT have 
   # "glcp-production" or "glcp-not-production")         
'''

# ===== GLCP-45829 ===================================
__help_apply_topics = '''
  repo-ops.py apply-topics myfoobar cool-stuff --repos abc xyz blah
  repo-ops.py apply-topics --repos abc xyz blah -- myfoobar cool-stuff
   # Apply the GitHub topics "myfoobar" and "cool-stuff" to 
   # repos "abc", "xyz", and "blah"
 '''

# ===== GLCP-45680 ===================================
__help_create_report = '''
  repo-ops.py create-report --master-report
   # Create the master CSV file

  repo-ops.py create-report --each-manager
   # Create a CSV file for each second line manager that includes only
   # repos with the topic "glcp-production", "glcp-not-production", 
   # OR "glcp-unknown"
'''

__help_apply_topics = f'''{__help_apply_topics}
  repo-ops.py apply-topics --with-missing-topics
   # Apply the GitHub topic "glcp-unknown" in repos that don't already have
   # "glcp-production", "glcp-not-production", or "glcp-unknown"
'''

# ===== GLCP-43983 ===================================
__help_apply_topics = f'''{__help_apply_topics}
  repo-ops.py apply-topics --with-ci-required
   # Apply the default GitHub topic "ci-required" to the repos that require CI

  repo-ops.py apply-topics foo bar --with-ci-required
   # Apply the GitHub topic "foo" and "bar" to the repos that require CI
'''

__help_create_report = f'''{__help_create_report}
  repo-ops.py create-report --with-ci
   # Create a CSV file with repos that have the default topic "ci-required"

  repo-ops.py create-report foo bar --with-ci
   # Create a CSV file with repos that have the topic "foo" OR "bar" 
'''

import argparse
import csv
import io
import itertools
import logging
import os
import pickle
import smtplib
import sys
from argparse import RawTextHelpFormatter
from collections import defaultdict
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http import HTTPStatus
from typing import Dict, List, Tuple, Union

import requests
import yaml
from func_timeout import FunctionTimedOut, func_timeout
from github import Github, GithubException
from github.ContentFile import ContentFile
from github.GithubException import UnknownObjectException
from github.PaginatedList import PaginatedList
from github.Repository import Repository

import common_utils as cu


gha: Github = None
github_token: str = ''
logger: logging.Logger = None

DEFAULT_TOPIC_NAME_SINGLE_REPORT = 'ci-required'
TOPIC_FOR_PROD = 'glcp-production'
TOPIC_NOT_FOR_PROD = 'glcp-not-production'
TOPIC_UNKNOWN = 'glcp-unknown'
TOPICS_WANTED = [TOPIC_FOR_PROD, TOPIC_NOT_FOR_PROD, TOPIC_UNKNOWN]

CSV_FIELD_NAMES = ['Repo name', 'Repo URL', 'Developer', 'Manager', 'Director',
                   'Primary language', 'Other languages', 'Topics']

THIS_DIR = os.path.dirname(__file__) if __file__.startswith('/') else '.'
LOGDIR = f'{THIS_DIR}/logdir'
MANUAL_REPO_LIST = 'files/repo-manual-list.yaml'
POLICY_REPORTS_FOLDER_NAME = 'devops-reports/policy-reports/managers'
URL_POLICYREPORTS_FOLDER_NAME = os.path.relpath(POLICY_REPORTS_FOLDER_NAME, 'devops-reports')
INFO_CSV = f'{POLICY_REPORTS_FOLDER_NAME}/../repos-ci-required.csv'
ALL_INFO_CSV = f'{POLICY_REPORTS_FOLDER_NAME}/../all-repos.csv'


def main() -> None:
    """
    The entry point into this Python script.
    """

    # The key is the name of the subcmd.
    # The value is the name of the logfile.
    subcmd_mapping = {
        'apply-topics': 'apply-topics',
          'at': 'apply-topics',
        'create-report': 'create-report',
          'cr': 'create-report',
        'email-report': 'email-report',
          'er': 'email-report',
        'remove-topics': 'remove-topics',
        'rt': 'remove-topics',
    }

    args = process_cmd_args()
    if args.verbose_level == 'none':
        logfile = '/dev/null'
    else:
        logfile = subcmd_mapping[args.subcmd]
        cu.mkdir_p(LOGDIR)
        logfile = f'{LOGDIR}/{logfile}.log'

    global logger
    logger = cu.get_logger('repo-ops', logfile,
                           level=args.verbose_level, output_to_console=True)

    if subcmd_mapping[args.subcmd] not in ['email-report']:
        # check_env_vars(['GITHUB_TOKEN', 'LDAP_PASSWD'])
        # TODO uncomment above when LDAP is accessible
        check_env_vars(['GITHUB_TOKEN'])

    global github_token, gha
    github_token = os.environ.get("GITHUB_TOKEN")
    gha = Github(github_token)

    logger.debug(f'the args are: {args}')
    args.func(args)
    print('all done... good bye')


def do_apply_topics(args: argparse.Namespace) -> None:
    """
    This function is called when 'repo-ops.py apply-topics ...' is invoked.
    """

    if args.with_missing_topics:
        apply_topic_unknown(args.wanted_topic_names, args.org_name)
    elif args.with_ci_required:
        apply_topics_for_CI(args.wanted_topic_names, args.org_name)
    elif args.the_repos:
        apply_topics_to_repos(args.the_repos, args.wanted_topic_names, args.org_name)
    elif args.the_topic:
        apply_topics_to_matching_repos(args.the_topic,
                                       args.wanted_topic_names, args.org_name)


def do_create_report(args: argparse.Namespace) -> None:
    """
    This function is called when 'repo-ops.py create-report ...' is invoked.
    """

    if args.report_for_mgr:
        create_report_per_mgr(args.wanted_topic_names)
    elif args.master_report:
        create_master_report(args.org_name)
    elif args.ci_report:
        create_single_report_for_CI(args.wanted_topic_names, args.org_name)


def do_email_report(args: argparse.Namespace) -> None:
    """
    This function is called when 'repo-ops.py email-report ...' is invoked.
    """

    check_env_vars(['GITHUB_REF_NAME', 'EMAIL_FROM',
                    'EMAIL_SERVER_NAME', 'EMAIL_SERVER_PORTNUM',
                    'EMAIL_SERVER_USERNAME', 'EMAIL_SERVER_PASSWORD'])
    if args.email_report_for_mgr:
        filenames = create_report_per_mgr(TOPICS_WANTED)
        email_report_per_mgr(filenames)
    elif args.email_unknown_repos:
        email_repos_unknown()


def do_remove_topics(args: argparse.Namespace) -> None:
    """
    This function is called when 'repo-ops.py remove-topics ...' is invoked.
    """
    if args.with_lower_priority:
        remove_topics_lower_priority(args.org_name)
    elif args.the_repos:
        logger.warning('under construction')


def apply_the_topics(wanted_topic_names: List[str], the_repo: Union[str, Repository],
                     the_current_topic_names: List[str] = None,
                     org_name='glcp') -> bool:
    """
    Add the wanted topics to the specified repo.
    Return False if the repo doesn't exist, or if all of the wanted topics
    already exist.
    Otherwise, return True.
    """

    if type(the_repo) is str:
        repo: Repository = get_repo_obj(the_repo, org_name)
        if repo is None:
            logger.warning(f'skipping... repo "{the_repo}" does not exist')
            return False
    else:
        repo: Repository = the_repo

    if the_current_topic_names is None:
        current_topics: List[str] = repo.get_topics()
        logger.debug(f'repo "{repo.name}" has current topics: {current_topics}')
    else:
        current_topics: List[str] = the_current_topic_names
        logger.debug(f'repo "{repo.name}" with current topics passed in: {current_topics}')

    # https://stackoverflow.com/questions/15147751/how-to-check-if-all-items-in-a-list-are-there-in-another-list
    if set(wanted_topic_names).issubset(current_topics):
        logger.debug(f'skipping... repo "{repo.name}" already has '
                     f'ALL of {wanted_topic_names} topics')
        return False

    topics: List[str] = []
    topics.extend(current_topics)
    topics.extend(wanted_topic_names)
    topics = list(dict.fromkeys(topics))  # remove duplicates
    repo.replace_topics(topics)  # TODO uncomment
    logger.info(f'Repo "{repo.name}": done adding topic(s) {wanted_topic_names}; '
                f'repo now has topics: {topics}')
    return True


def apply_topics_for_CI(wanted_topic_names: List[str], org_name: str) -> None:
    """
    Add the desired topic(s) to the repos.  Existing topics are preserved.

    """

    repo_names: List[str] = []

    # TODO uncomment
    x = retrieve_ccs_repos(org_name)
    logger.info(f'There are {len(x)} CCS repos that have been identified as having CI')
    repo_names.extend(x)

    # TODO uncomment
    x = retrieve_harmony_repos(org_name)
    logger.info(f'There are {len(x)} Harmony repos')
    repo_names.extend(x)

    x = retrieve_repos_from_manual_list()
    repo_names.extend(x)
    logger.info(f'There are {len(x)} repos from manual list')

    # remove any duplicate repo names
    repo_names = list(dict.fromkeys(repo_names))
    logger.info(f'There are a total of {len(repo_names)} repos from the combined lists')

    for repo_name in repo_names:
        apply_the_topics(wanted_topic_names, repo_name)


def apply_topic_unknown(wanted_topic_names: List[str], org_name: str) -> None:
    """
    Add the GitHub topic "glcp-unknown" to a repo if the repo doesn't already have
    (is missing) one of these topics:
      'glcp-production'
      'glcp-not-production'
      'glcp-unknown'
    """

    logger.debug('getting repos...')
    repos: PaginatedList[Repository] = gha.get_organization(org_name).get_repos()
    # repos = gha.search_repositories(query=f'org:{org_name} topic:myfoobar') # TODO test remove

    count = 0
    for repo in repos:
        if repo.archived:
            logger.warning(f'skipping... repo "{repo.name}" is archived')
            continue
        current_topics: List[str] = repo.get_topics()
        if is_any_wanted_topic_exist(wanted_topic_names, current_topics):
            continue
        logger.debug(f'repo "{repo.name}" has topic(s) {current_topics} but '
                     f'none of the wanted topics {wanted_topic_names} is present')
        logger.debug(f'applying topic "{TOPIC_UNKNOWN}" to repo "{repo.name}"')
        if apply_the_topics([TOPIC_UNKNOWN], repo):
            count += 1
        logger.debug('-' * 20)

    if count > 0:
        logger.info(f'The topic "{TOPIC_UNKNOWN}" was added to {count} '
                    'repos because they did not have any of the '
                    f'wanted topics {wanted_topic_names}')
    else:
        logger.info(f'The wanted topics {wanted_topic_names} were not '
                    'missing in the repos')


def apply_topics_to_matching_repos(the_topic: str, wanted_topic_names: List[str],
                                   org_name: str) -> None:
    """
    Add the wanted topics to repos that currently have the <the_topic> topic.
    """

    logger.debug(f'searching for repos with the topic "{the_topic}"')

    data: List[Dict[str, str]] = read_csv_file(ALL_INFO_CSV, [the_topic])
    for row in data:
        repo_name = row[CSV_FIELD_NAMES[0]]
        # current_topics: List[str] = row[CSV_FIELD_NAMES[7]].split(',')
        # if not is_any_wanted_topic_exist([the_topic], current_topics):
        #     logger.debug(f'skipping... repo "{repo_name}" does not have '
        #                  f'"{the_topic}" topic')
        #     continue
        # the current repo has the_topic, so add the wanted_topic_names

        apply_the_topics(wanted_topic_names, repo_name)


def apply_topics_to_repos(repo_names: List[str],
                          wanted_topic_names: List[str], org_name: str) -> None:
    """
    Add the user-specified topics to the user-specified repos.
    """

    logger.debug('processing repos...')
    for repo_name in repo_names:
        apply_the_topics(wanted_topic_names, repo_name)


def check_env_vars(evars: List[str]) -> None:
    """
    Check whether the specified environment vars are set.
    If not, exit this script.
    """

    need_evars = False
    for evar in evars:
        if evar not in os.environ:
            print(f'ERROR: Env var "{evar}" must to be set.')
            need_evars = True
    if need_evars:
        sys.exit(1)


def create_master_report(org_name: str) -> None:
    """
    Create the master CSV file.  The CSV file will contain all repos
    with any GitHub topics.  Repos that are archived will be excluded.
    """

    mgr_info: Dict[str, str] = retrieve_mgr_reporting_info_from_pickle_file()
    u2e: Dict[str, str] = retrieve_username_to_email_mapping()
    csv_data: list[tuple[str, str, str, str, str, str, str, str]] = []
    skip_count: int = 0

    repos: PaginatedList[Repository] = gha.get_organization(org_name).get_repos()
    # TODO uncomment above; remove below
    # repos = gha.search_repositories(query=f'org:{org_name} topic:myfoobar')
    # repos = sorted(repos, key=lambda x: x.name)  # may take too long

    for repo in repos:
        if repo.archived:
            logger.warning(f'skipping... repo "{repo.name}" is archived')
            skip_count += 1
            continue
        primary_lang = repo.language
        #if primary_lang is None:
        #   logger.warning(f'skipping... repo "{repo.name}" has no primary language')
        #   skip_count += 1
        #   continue

        # repo.get_languages() returns a dict where the first key is the primary language
        # and the remaining keys (index 1 to the end of list) are the other languages.
        other_langs = ','.join(list(repo.get_languages().keys())[1:]) or '-none-'
        logger.debug(f'repo "{repo.name}" has primary language={primary_lang} and '
                     f'other lang={other_langs}')

        mgr2_email, engr_email = get_director_and_delegate_email_addrs(repo)
        if engr_email is None:
            engr_email: str = get_contributor_email_addr(repo, u2e) or '-unknown-'
            logger.debug(f'repo "{repo.name}": engr_email={engr_email} '
                         '(because delegate NOT found or .hpe/owners.yml NOT exist)')
        mgr1_email: str = mgr_info.get(engr_email, None) or '-unknown-'
        if mgr2_email is None:
            mgr2_email: str = mgr_info.get(mgr1_email, None) or '-unknown-'
            if get_reporting_chain_level_num(mgr2_email, mgr_info) <= 2:
                logger.debug(f'repo "{repo.name}": {mgr2_email} (manager of "{mgr1_email}") '
                             f'is 2 levels (or less) below the CEO, so treating '
                             f'"{mgr1_email}" as the second line manager'
                            )
                mgr2_email = mgr1_email
            logger.debug(f'repo "{repo.name}": mgr2_email={mgr2_email} '
                         '(because director NOT found or .hpe/owners.yml NOT exist)')

        #-# Cannot use at this time.
        #-# The on-prem runners do NOT have access to HPE LDAP
        # mgr1_email, mgr2_email = get_mgr_and_dir_ldap(email_addr)

        csv_data.append(
            (repo.name, f'https://github.com/{org_name}/{repo.name}',
             engr_email, mgr1_email, mgr2_email,
             primary_lang or '-none-', other_langs, ','.join(repo.get_topics()))
        )

    # csv_data = sorted(csv_data, key=lambda x: x[0])
    # this sorts by the first column, so no need for the 'key' arg
    csv_data = sorted(csv_data)
    logger.info(f'Number of repos kept: {len(csv_data)}')
    logger.info(f'Number of repos skipped: {skip_count}')
    # logger.info(f'number repos with one or more of these '
    #             f'topics {wanted_topic_names} : {len(csv_data)}')
    create_csv_file(csv_data, ALL_INFO_CSV)


def create_report_per_mgr(wanted_topic_names: List[str]) -> List[str]:
    """
    Create a CSV file for each second line manager.
    Return the list of CSV filenames that were created for each second 
    line manager.
    """

    data: List[Dict[str, str]] = read_csv_file(ALL_INFO_CSV, wanted_topic_names)

    # https://stackoverflow.com/questions/26367812/appending-to-list-in-python-dictionary
    repos_per_mgr: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    # https://stackoverflow.com/questions/64667466/group-list-of-dicts-by-key-in-python
    for k, v in itertools.groupby(data, key=lambda x: x[CSV_FIELD_NAMES[4]]):
        repos_per_mgr[k].extend(list(v))

    folder = f'{THIS_DIR}/{POLICY_REPORTS_FOLDER_NAME}'
    cu.mkdir_p(folder)

    csv_files = []
    for mgr_email in repos_per_mgr:
        csv_data: List[Tuple[str, ...]] = []
        for repo_row in repos_per_mgr[mgr_email]:
            # each repo_row is a Dict[str, str]
            csv_data.append(tuple(repo_row.values()))
        filename = f'{mgr_email.split("@")[0]}.csv'
        create_csv_file(csv_data, f'{folder}/{filename}')
        csv_files.append(filename)

    return csv_files


def create_single_report_for_CI(wanted_topic_names: List[str], org_name: str):
    """
    Create a CSV file with repos that have the "ci-required" GitHub topic.
    """

    csv_data: list[tuple[str, str, str, str, str, str, str, str]] = []
    data: List[Dict[str, str]] = read_csv_file(ALL_INFO_CSV, wanted_topic_names)
    skip_count: int = 0

    for row in data:
        repo_name = row[CSV_FIELD_NAMES[0]]
        engr_email = row[CSV_FIELD_NAMES[2]]
        mgr1_email = row[CSV_FIELD_NAMES[3]]
        mgr2_email = row[CSV_FIELD_NAMES[4]]
        primary_lang = row[CSV_FIELD_NAMES[5]]
        other_langs = row[CSV_FIELD_NAMES[6]]
        topics = row[CSV_FIELD_NAMES[7]].split(',')

        csv_data.append(
            (repo_name, f'https://github.com/{org_name}/{repo_name}',
             engr_email or '-unknown-', mgr1_email or '-unknown-', mgr2_email or '-unknown-',
             primary_lang or '-none-', other_langs or '-none-', ','.join(topics))
        )

    # csv_data = sorted(csv_data, key=lambda x: x[0])
    # this sorts by the first column, so no need for the 'key' arg
    csv_data = sorted(csv_data)
    logger.info(f'Number of repos kept: {len(csv_data)}')
    logger.info(f'Number of repos skipped: {skip_count}')
    # logger.info(f'number repos with one or more of these '
    #             f'topics {wanted_topic_names} : {len(csv_data)}')
    create_csv_file(csv_data, INFO_CSV)


def create_csv_file(data: List[Tuple[str, ...]], filename: str) -> None:
    """
    Create a CSV file containing the following info:
       - repo name
       - repo URL
       - developer email-addr
       - manager email-addr
       - director email-addr
       - primary language
       - other languages
       - GitHub topic
    :rtype: None
    :param data: the info to be saved to the CSV file
    :type data: a list of Tuple containing the above info (all strings)
    :param filename: the name of the CSV file
    :type filename: string
    """

    with open(filename, 'w', newline='') as fh:
        wr = csv.writer(fh, quoting=csv.QUOTE_ALL)
        wr.writerow(CSV_FIELD_NAMES)
        wr.writerows(data)
    logger.info(f'Done creating file "{filename}"')


def email_repos_unknown() -> None:
    """
    Send email to the developer and first line manager about each repo
    that has ONLY the "glcp-unknown" GitHub topic to take the appropriate
    action.  Repos that have "glcp-unknown" in addition to "glcp-production"
    or "glcp-not-production" will be ignored.
    """

    wanted_topic_names = [TOPIC_FOR_PROD, TOPIC_NOT_FOR_PROD]
    data: List[Dict[str, str]] = read_csv_file(ALL_INFO_CSV, [TOPIC_UNKNOWN], skip=False)

    subject_fmt = 'ACTION REQUIRED: GitHub repository {repo_name} owned by you'
    body_fmt = '''
    Hi {engr}, {mgr}<br><br>
    The GitHub repo <a href="https://github.com/glcp/{repo_name}">{repo_name}</a> 
    currently has the GitHub topic "<b>glcp-unknown</b>".<br><br>
    This email is sent out daily until one of the actions below is taken.<br><br>
    <b>Action required:</b><br>
    <ol>
     <li>Please ensure that the repository is correctly marked as Production or 
       Non-Production. For instructions and what is considered Production, please   
       see <a href="https://hpe.atlassian.net/l/cp/0R1H11n6">Confluence</a>.</li>
     <li>If the repository is no longer needed, please see FAQ on how to 
       <a href="https://hpe.atlassian.net/l/cp/0R1H11n6#archive-repo">archive</a>
       the repo.</li>
     <li>If you (or your team) are not the owner, please see FAQ on how to  
       <a href="https://hpe.atlassian.net/l/cp/0R1H11n6#daily-emails">stop</a> 
       getting these daily emails.</li>  
    </ol><br>
    '''

    count = 0
    skip_count = 0
    for repo_row in data:
        # each repo_row is a Dict[str, str]
        repo_name: str = repo_row[CSV_FIELD_NAMES[0]]
        repo: Repository = get_repo_obj(repo_name)
        if repo is None:
            logger.warning(f'skipping... repo "{repo_name}" does NOT exist')
            logger.debug('-' * 20)
            skip_count += 1
            continue
        # current_topics: List[str] = repo_row[CSV_FIELD_NAMES[7]].split(',')
        # the current topics in the CSV file might be out of date, so we
        # need to get the latest topics from GitHub
        current_topics: List[str] = repo.get_topics()

        if is_any_wanted_topic_exist(wanted_topic_names, current_topics):
            logger.debug(f'skipping... repo "{repo_name}" has '
                         f'{current_topics} topics (including one of '
                         f'{wanted_topic_names} topics)')
            logger.debug('-' * 20)
            skip_count += 1
            continue
        if not is_any_wanted_topic_exist([TOPIC_UNKNOWN], current_topics):
            logger.debug(f'skipping... repo "{repo_name}" does NOT have'
                         f'topic {TOPIC_UNKNOWN}')
            logger.debug('-' * 20)
            skip_count += 1
            continue
        count += 1
        logger.info(f'{count}: Repo "{repo_name}": sending out nag email because '
                    f'repo has topic(s) {current_topics} (including "{TOPIC_UNKNOWN}") '
                    f'but none of the wanted topics {wanted_topic_names} is present')
        engr_email = repo_row[CSV_FIELD_NAMES[2]]
        mgr1_email = repo_row[CSV_FIELD_NAMES[3]]
        send_email([engr_email, mgr1_email],
                   subject=subject_fmt.format(repo_name=repo_name),
                   body=body_fmt.format(engr=engr_email,
                                        mgr=mgr1_email,
                                        repo_name=repo_name)
                  )
        logger.debug('-' * 20)
    logger.info(f'Number of emails NOT sent (repos skipped): {skip_count}')
    logger.info(f'Number of emails sent: {count}')


def email_report_per_mgr(filenames: List[str]) -> None:
    """
    Email the CSV file to each second line manager.
    """

    subject = 'Monthly report: GitHub repositories owned by your team'
    body_fmt = ''' 
    <html><head></head><body>
       Hi {to_email_addr}<br><br>
       This is a friendly monthly reminder about the GLCP GitHub repository management.
       Your reports have been identified as the owner of one or more GitHub  
       repositories.  Please work with your team to ensure the repositories 
       are correctly categorized as Production or Non-Production. Optionally, archive 
       the repositories that are no longer needed.<br><br>
       Summary of repositories owned:
       <ul>
         <li>{counter_prod} Production repositories</li>
         <li>{counter_not_prod} Non-Production repositories</li>
         <li>{counter_unknown} repositories not yet classified as Production 
             or Non-Production</li>
       </ul>
       Details for each of these repositories, including the most active developer, 
       are in the attached CSV file.  You can also <a href="{url}">view</a> 
       the CSV file at GitHub.<br><br>
       <b>Action required:</b><br>
       <ol>
        <li>Work with the developer and manager to ensure all repositories 
          (that are owned by your team) in the attached CSV file are correctly 
          marked as Production or Non-Production. For instructions and 
          what is considered Production, please see 
          <a href="https://hpe.atlassian.net/l/cp/0R1H11n6">Confluence</a>.</li>
        <li>If a repository is no longer needed, please 
          <a href="https://hpe.atlassian.net/servicedesk/customer/portal/2">
          file a ticket</a> with GLCP RelEng to have the repository archived.</li>
       </ol>
    </body></html>
    '''

    folder = f'{THIS_DIR}/{POLICY_REPORTS_FOLDER_NAME}'

    for filename in filenames:
        logger.debug(f'current filename is {filename}')
        data: List[Dict[str, str]] = read_csv_file(f'{folder}/{filename}', TOPICS_WANTED)
        mgr2_email = f'{filename.split(".csv")[0]}@hpe.com'

        counter = {TOPIC_FOR_PROD: 0, TOPIC_NOT_FOR_PROD: 0, TOPIC_UNKNOWN: 0}
        for repo_row in data:
            # each repo_row is a Dict[str, str]
            for t in (TOPIC_FOR_PROD, TOPIC_NOT_FOR_PROD, TOPIC_UNKNOWN):
                if t in repo_row[CSV_FIELD_NAMES[7]]:
                    logger.debug(f'file "{filename}": repo "{repo_row[CSV_FIELD_NAMES[0]]}" has topic "{t}"')
                    counter[t] += 1
                    break
        import json
        logger.debug(f'mgr "{mgr2_email}": counter info is: {json.dumps(counter, indent=2)}')

        url = f'https://github.com/glcp/devops-reports/blob/' \
              f'{os.environ["GITHUB_REF_NAME"]}/{URL_POLICYREPORTS_FOLDER_NAME}/{filename}'

        body = body_fmt.format(to_email_addr=mgr2_email, url=url,
                               counter_prod=counter[TOPIC_FOR_PROD],
                               counter_not_prod=counter[TOPIC_NOT_FOR_PROD],
                               counter_unknown=counter[TOPIC_UNKNOWN]
                               )
        send_email([mgr2_email], subject, body, filename)


def get_contributor_email_addr(repo: Repository,
                               user_email_mapping: Dict[str, str]) -> str:
    """
    Get the email address of the top contributor.  If the top contributor's
    email address cannot be found, then go to the second top contributor.
    Keep repeating until an email address is found, or return an empty string.
    """

    for c in repo.get_contributors():
        engr_email = user_email_mapping.get(c.login, None)
        logger.debug(f'repo "{repo.name}": current contributor="{c.login}"; '
                     f'email addr="{engr_email}"')
        if engr_email is not None:
            logger.debug(f'repo "{repo.name}": found contributor="{c.login}" '
                         f'with matching email addr="{engr_email}"')
            return engr_email
    logger.warning(f'Repo "{repo.name}": cannot find matching email address')
    return ''


def get_director_and_delegate_email_addrs(repo: Repository) -> \
        Tuple[Union[str, None], Union[str, None]]:
    """
    If the ".hpe/owners.yml" file exists in the repo, then:
      - the value of the "director" key will be the second-line manager
      - the first delegate in the file will be the main developer/contributor
        If the "delegates" key doesn't exist, then return None
    If the ".hpe/owners.yml" file doesn't exist in the repo, then:
      return None, None
    """

    mgr2_email: Union[str, None] = None
    contributor: Union[str, None] = None

    if (contents := get_file_contents('.hpe/owners.yml', repo)) is not None:
        logger.debug(f'repo "{repo.name}": file ".hpe/owners.yml" exists')
        yaml_contents = yaml.safe_load(contents)
        mgr2_email = yaml_contents['director']
        try:
            contributor = yaml_contents['delegates'][0]
        except Exception:
            logger.warning(f'repo "{repo.name}": could not get the first delegate')
            contributor = None
        logger.debug(f'repo "{repo.name}": director={mgr2_email}; '
                     f'first delegate={contributor}')
    else:
        logger.debug(f'repo "{repo.name}": file ".hpe/owners.yml" does NOT exist')
        contributor = None

    return mgr2_email, contributor


def get_file_contents(wanted_file: str, repo: Repository) -> Union[str, None]:
    """
    Retrieve the file contents of the specified file from the specified repo.
    """

    contents: str = ''
    try:
        cf: ContentFile = repo.get_contents(wanted_file)
        contents: str = cf.decoded_content.decode()
    except UnknownObjectException as e:
        if e.status == HTTPStatus.NOT_FOUND:
            logger.warning(f'file "{wanted_file}" NOT found in repo "{repo.name}"')
            return None
        else:
            logger.error(f'repo "{repo.name}": some other error occurred: {e}')
            raise
    except GithubException as e:
        if e.status == HTTPStatus.NOT_FOUND:
            logger.warning(f'repo "{repo.name}" is empty')
            return None
        else:
            logger.error(f'repo "{repo.name}": some other error occurred: {e}')
            raise
    except Exception as e:
        logger.error(f'repo "{repo.name}": exception occurred: {e}')
        raise

    return contents


def get_mgr_and_dir_ldap(email_addr: str) -> Tuple[str, str]:
    """
    #-# Cannot use at this time.  The on-prem runners do NOT have access to HPE LDAP

    Do a lookup of the first line manager and second line manager of the given
    user's email address.

    :param email_addr: email address
    :type email_addr: str
    """

    try:
        data = func_timeout(10, cu.get_ldap_reporting_chain, args=(email_addr,))
        mgr_email = data[1]['Email']
        director_email = data[2]['Email']
    except FunctionTimedOut as e:
        mgr_email = 'cannot-determine'
        director_email = 'cannot-determine'
        logger.warning(f'Cannot retrieve reporting chain info:\n{e}\n')
    except Exception as e:
        mgr_email = 'cannot-determine'
        director_email = 'cannot-determine'
        logger.warning(f'Something went wrong while getting reporting chain info:\n{e}\n')
    return mgr_email, director_email


def get_repo_obj(repo_name: str, org_name: str = 'glcp') -> Union[None, Repository]:
    """
    Return the Repository object of the specified repo name.
    Return None if the repo doesn't exist.
    """

    try:
        repo: Repository = gha.get_repo(f'{org_name}/{repo_name}')
    except UnknownObjectException as e:
        if e.status == HTTPStatus.NOT_FOUND:
            logger.warning(f'repo "{repo_name}" does not exist')
            return None
        else:
            logger.error(f'repo "{repo_name}": some other error occurred: {e}')
            raise
    except Exception as e:
        logger.error(f'repo "{repo_name}": exception occurred: {e}')
        raise

    return repo


def get_reporting_chain_level_num(user_email: str, mgr_info: Dict[str, str]) -> int:
    """
    Return the level number of the specified user in the reporting chain.
     . level 0 is the CEO
     . level 1 is the CEO's direct report
     . level 2 is the direct report of the CEO's direct report
     ... and so on.
    """

    if user_email == '-unknown-':
        return 999

    def construct_report_chain(user_email, chain=None):
        """
        Construct the reporting chain, starting from the specified user
        up to the CEO.
        """

        if chain is None:
            chain = []
        chain.append(user_email)  # build up the reporting chain
        if user_email == mgr_info[user_email]:
            # we've reached the top
            return chain
        else:
            # recursively go up the chain of command
            return construct_report_chain(mgr_info[user_email], chain)

    # the first item in the list is the specified user
    # and the last item is the CEO
    report_chain = construct_report_chain(user_email)

    report_chain_level: Dict[str, int] = {}
    # reverse the reporting chain to construct a mapping
    # of email address -> reporting level
    # with level 0 being the CEO and the last level being the specified user
    for level, email_addr in enumerate(reversed(report_chain)):
        report_chain_level[email_addr] = level
        # logger.debug(f'> {email_addr}: {level}')

    return report_chain_level[user_email]


def is_any_wanted_topic_exist(wanted_topic_names: List[str],
                              current_topic_names: List[str]) -> bool:
    """
    Return true if any item in the wanted_topic_names list exists in the
    current_topic_names list.
    """

    # list2 --> topics; list1 --> wanted_topic_names
    # https://stackoverflow.com/questions/16138015/checking-if-any-elements-in-one-list-are
    # check if any item in list1 exists in list2
    return len(set(wanted_topic_names).intersection(current_topic_names)) > 0


def read_csv_file(filename: str, wanted_topic_names: List[str],
                  skip: bool = True) -> List[Dict[str, str]]:
    """
    Read the given CSV file.  Any row that doesn't have one of the wanted_topic_names
    will be ignored.  Any row that doesn't have a valid second line manager email
    address will also be ignored.
    if skip is False, then all rows are kept.
    """

    data: List[Dict[str, str]] = []
    skip_count = 0

    logger.debug(f'reading CSV file {filename}')
    with open(filename, 'r') as fh:
        # csv_reader = csv.reader(fh, delimiter=',')
        csv_reader = csv.DictReader(fh, delimiter=',', fieldnames=CSV_FIELD_NAMES)
        next(csv_reader)  # skip the header row

        row: Dict[str, str]
        for row in csv_reader:
            repo_name: str = row[CSV_FIELD_NAMES[0]]
            topics: List[str] = row[CSV_FIELD_NAMES[7]].split(',')

            if skip and not is_any_wanted_topic_exist(wanted_topic_names, topics):
                skip_count += 1
                logger.debug(f'skipping... repo "{repo_name}" has topic(s) {topics} but '
                             f'none of the wanted topics {wanted_topic_names} is present')
                continue
            if skip and (mgr2_email := row[CSV_FIELD_NAMES[4]]) == '-unknown-':
                skip_count += 1
                logger.warning(f'skipping... repo "{repo_name}" has '
                               f'unknown second line mgr="{mgr2_email}"')
                continue
            data.append(row)
    logger.debug(f'number of rows kept: {len(data)}')
    logger.debug(f'number of rows skipped: {skip_count}')

    return data


def remove_topics_lower_priority(org_name: str) -> None:
    """
    Remove the lower priority topics from the repos.
       "glcp-production" (highest priority)
       "glcp-not-production"
       "glcp-unknown" (lowest priority)
     A repo with all 3 topics will end up with only the "glcp-production" topic
     A repo with "glcp-production" and "glcp-not-production" will end up with
      only the "glcp-production" topic
     A repo with "glcp-production" and "glcp-unknown" will end up with
      only the "glcp-production" topic
     A repo with "glcp-not-production" and "glcp-unknown" will end up with
      only the "glcp-not-production" topic
    """

    data: List[Dict[str, str]] = read_csv_file(ALL_INFO_CSV, TOPICS_WANTED, skip=False)
    count = 0
    skip_count = 0
    for repo_row in data:  # each repo_row is a Dict[str, str]
        repo_name: str = repo_row[CSV_FIELD_NAMES[0]]
        repo: Repository = get_repo_obj(repo_name, org_name)
        if repo is None:
            logger.warning(f'skipping... repo "{repo_name}" does NOT exist')
            logger.debug('-' * 20)
            skip_count += 1
            continue
        # current_topics: List[str] = repo_row[CSV_FIELD_NAMES[7]].split(',')
        # the current topics in the CSV file might be out of date, so we
        # need to get the latest topics from GitHub
        current_topics: List[str] = repo.get_topics()

        # if only one of the wanted topics is present in the current topics,
        # then that one topic is the highest priority topic and there are no
        # lower priority topics
        if len(the_items := set(TOPICS_WANTED).intersection(current_topics)) <= 1:
            msg = f'only "{list(the_items)[0]}"' if len(the_items) > 0 else 'nothing'
            logger.debug(f'skipping repo "{repo_name}": nothing to update because '
                         f'{msg} (out of {TOPICS_WANTED}) is '
                         f'present in current topics {current_topics}')
            logger.debug('-' * 20)
            skip_count += 1
            continue

        count += 1
        logger.debug(f'{count}: before cleanup: repo "{repo_name}" has '
                     f'current_topics={current_topics}')

        highest_priority_topic_name = TOPIC_UNKNOWN
        for t in TOPICS_WANTED:
            if t in current_topics:
                highest_priority_topic_name = t
                logger.debug(f'{count}: repo "{repo_name}" has highest priority '
                             f'topic="{highest_priority_topic_name}"')
                break

        # Remove all "glcp-production", "glcp-not-production", "glcp-unknown"
        current_topics = [t for t in current_topics if t not in TOPICS_WANTED]

        logger.debug(f'{count}: after cleanup: repo "{repo_name}" has '
                     f'current_topics={current_topics}')
        logger.debug(f'{count}: applying highest priority topic '
                     f'"{highest_priority_topic_name}" to repo "{repo_name}"')

        if apply_the_topics([highest_priority_topic_name], repo, current_topics):
            count += 1
        logger.debug('-' * 20)

    logger.info(f'Number of repos updated: {count}')
    logger.info(f'Number of repos skipped: {skip_count}')


def retrieve_ccs_repos(org_name: str) -> List[str]:
    """
    Return the repos that have the "deploy/app-brigade-manifest.json" file.
    """

    repos_with_ci: List = []
    repos: PaginatedList[Repository] = gha.get_organization(org_name).get_repos()
    # repos = sorted(repos, key=lambda x: x.name)  # may take too long

    # TODO test remove
    # repos = gha.search_repositories(query=f'org:{org_name} rmc')

    # TODO test remove
    # with open('/tmp/repos-in-github.txt,blah', 'w') as f:
    #     f.write('\n'.join(repos))
    # TODO test remove
    # with open('temp-repo-list.txt') as f:
    #     repos = f.read().splitlines()

    wanted_file = 'deploy/app-brigade-manifest.json'
    # look for CCS prod repos
    for repo in repos:
        if repo.archived:
            logger.warning(f'skipping... repo "{repo.name}" is archived')
            continue
        # logger.debug(f'current repo={repo.name}')
        if get_file_contents(wanted_file, repo) is None:
            continue
        logger.debug(f'repo "{repo.name}" has file {wanted_file}')
        repos_with_ci.append(repo.name)

    repos_with_ci = sorted(repos_with_ci)
    # TODO test remove
    # with open('/tmp/repos-with-ci.txt,blah', 'w') as f:
    #     f.write('\n'.join(repos_with_ci))

    return repos_with_ci


def retrieve_harmony_repos(org_name: str) -> List[str]:
    """
    Parse the "manifests/harmony-version.yaml" file in the "harmony-versions" repo
    and return the repo names.
    """

    repo_name = 'harmony-versions'
    repo = gha.get_repo(f'{org_name}/{repo_name}')
    wanted_file = 'manifests/harmony-version.yaml'

    if (contents := get_file_contents(wanted_file, repo)) is None:
        logger.warning(f'skipping repo "{repo_name}" ... file "{wanted_file}" '
                       'does NOT exist')
        return []

    yaml_content = yaml.safe_load(contents)
    # exclude repos such as https://hpeartifacts.jfrog.io/artifactory/helm-harmony-ops
    repos = [i for i in dict.fromkeys(cu.get_values_from_dict('repo', yaml_content))
             if 'github' in i]

    repos.extend(dict.fromkeys(cu.get_values_from_dict('git-repo', yaml_content)))

    # example: value is https://github.com/glcp/sbt-harmony.git
    # splitting on '/' will return 'sbt-harmony.git' as the last
    # item (represented by the "-1").  Replace '.git' with empty string
    repos = sorted([i.split('/')[-1].replace('.git', '') for i in repos])

    return repos


def retrieve_repos_from_manual_list() -> List[str]:
    """
    Get the repos from the manual list.
    """

    with open(MANUAL_REPO_LIST) as fh:
        info: Dict[str, List[str]] = yaml.safe_load(fh)
    # for i in info['repo-names']
    #     print(i)
    return info['repo-names']


def retrieve_mgr_reporting_info_from_pickle_file() -> Dict[str, str]:
    """
    Return the mapping of user -> manager from "directory.pickle" file in
    the "hpe-directory" repo.
    This is a temp work-around until connectivity with HPE LDAP is established.
    """

    pickle_file_url = 'https://raw.githubusercontent.com/glcp/hpe-directory' \
                      '/main/directory/directory.pickle'
    logger.debug(f'getting reporting chain info from {pickle_file_url}')
    headers = {
        'Authorization': f'Bearer {github_token}',
        'Accept': 'application/vnd.github+json'
    }
    response = requests.get(pickle_file_url, headers=headers)
    response.raise_for_status()

    # https://stackoverflow.com/questions/72463563/is-there-a-function-to-download-a-pickle-file-via-requests-posturl-and-load-it
    pickle_obj = io.BytesIO(response.content)

    # https://stackoverflow.com/questions/35067957/how-to-read-pickle-file
    objects = []
    while True:
        try:
            objects.append(pickle.load(pickle_obj))
        except EOFError:
            break

    data = {}
    for mydict in objects:
        for i in mydict:
            # print(f"{i} {mydict[i]['manager']}")
            data[i] = mydict[i]['manager']

    # TODO temp remove
    # import json
    # with open ('reporting-chain.json', 'w') as fh:
    #     json.dump(data, fh, indent=2)

    return data


def retrieve_username_to_email_mapping() -> Dict[str, str]:
    """
    Get the mapping of GitHub username -> HPE email address from
    the "known_hpe_ids.txt" file in the "hpe-hcss/github-users" repo.
    """

    github_token = os.environ.get("GITHUB_TOKEN_OTHER")
    # fetch the latest user list for the HPE Enterprise
    user_list = 'https://raw.githubusercontent.com/hpe-hcss/github-users/master/known_hpe_ids.txt'
    logger.debug(f'getting username->email addr mapping from {user_list}')
    headers = {
        'Authorization': f'Bearer {github_token}',
        'Accept': 'application/vnd.github+json'
    }
    response = requests.get(user_list, headers=headers)
    response.raise_for_status()

    lines = response.text.splitlines()
    mapping: Dict[str, str] = {}
    # lines.sort(key=lambda x: x.lower())
    for line in lines:
        username, email_addr = line.split()
        mapping[username] = email_addr
    return mapping


def send_email(to_email_addrs: List[str], subject: str, body: str,
               filename: Union[str, None] = None) -> None:
    """
    Send out email.
    """

    if os.environ.get('DO_NOT_EMAIL', None):
        logger.debug(f'the env var "DO_NOT_EMAIL" is set... NOT sending email')
        return

    email_server_address = os.environ['EMAIL_SERVER_NAME']
    email_server_port = int(os.environ['EMAIL_SERVER_PORTNUM'])
    email_server_username = os.environ['EMAIL_SERVER_USERNAME']
    email_server_password = os.environ['EMAIL_SERVER_PASSWORD']
    email_from = os.environ['EMAIL_FROM']
    recipients = to_email_addrs
    # recipients = ['test.mail@hpe.com']  # TODO--

    msg = MIMEMultipart()
    msg['From'] = email_from
    msg['To'] = ', '.join(recipients)
    msg['Subject'] = subject

    # logger.debug(f'email body is:\n{body}')

    msg.attach(MIMEText(body, 'html'))
    if filename:
        attachment = MIMEBase('application', "octet-stream")
        with open(f'{THIS_DIR}/{POLICY_REPORTS_FOLDER_NAME}/{filename}', "rb") as fh:
            data = fh.read()
        attachment.set_payload(data)
        encoders.encode_base64(attachment)
        attachment.add_header('Content-Disposition', f'attachment; filename="{filename}"')
        msg.attach(attachment)

    with smtplib.SMTP_SSL(email_server_address, email_server_port) as s:
        s.login(email_server_username, email_server_password)
        try:
            logger.debug(f'sending email to recipients: {recipients}')
            s.send_message(msg, from_addr=email_from, to_addrs=recipients)
        except Exception as e:
            logger.error(f'Failed to send email to recipient(s) "{recipients}": {e}')
        s.quit()


def process_cmd_args() -> argparse.Namespace:
    """
    Get the commandline arguments.
    """

    desc = ''
    this_prog = os.path.basename(__file__)

    # https://stackoverflow.com/questions/21185526/custom-usage-function-in-argparse

    main_parser = argparse.ArgumentParser(
        prog='repo-ops',
        # https://stackoverflow.com/questions/5462873/control-formatting-of-the-argparse-help-argument-list
        # formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=45),
        formatter_class=RawTextHelpFormatter,
        description=desc)

    # https://stackoverflow.com/questions/24180527/argparse-required-arguments-listed-under-optional-arguments/
    # https://stackoverflow.com/questions/7498595/python-argparse-add-argument-to-multiple-subparsers
    common_optional_parser = argparse.ArgumentParser(add_help=False)
    common_optional_parser.add_argument(
        '-o', '--org', dest='org_name', default='glcp',
        help='the Github organization to use [default: "%(default)s"]')
    common_optional_parser.add_argument(
        '-ld', '--logdir', dest='logdir', default=LOGDIR,
        help='the top-level logdir where log messages will be '
             'written to\n[default: "%(default)s"]')
    common_optional_parser.add_argument(
        '-vl', '--verbose-level', dest='verbose_level',
        choices=['none', 'debug', 'info', 'warning', 'error', 'critical'],
        default='debug',
        help='set the verbose level of log messages [default: %(default)s]')

    # ----------------------------------------------------------------------------------------
    # https://stackoverflow.com/questions/16981106/how-to-change-the-text-optional-arguments-in-argparse
    main_parser._positionals.title = 'commands'

    # https://stackoverflow.com/questions/8250010/argparse-identify-which-subparser-was-used
    # subparsers = main_parser.add_subparsers(dest='subcmd', metavar='<mandatory command>')
    subparsers = main_parser.add_subparsers(dest='subcmd', required=True)
    # https://stackoverflow.com/questions/23349349/argparse-with-required-subparser
    # https://stackoverflow.com/questions/18282403/argparse-with-required-subcommands
    #     subparsers.required = True

    #----------------------------------------------------------------------------------------
    # https://stackoverflow.com/questions/13609118/make-pythons-argparse-accept-single-character-abbreviations-for-subparsers

    desc = 'Apply the GitHub topics to the GitHub repos'
    apply_topics_parser = subparsers.add_parser(
        'apply-topics', aliases=['at'],
        epilog=f'Examples:{__help_apply_topics}',
        help='apply GitHub topics',
        description=desc,
        formatter_class=RawTextHelpFormatter,
        parents=[common_optional_parser])
    apply_topics_parser.add_argument(
        'wanted_topic_names', nargs='*', metavar='<topic-name>',
        help='the topic name(s) to be applied ')
    atp_meg = apply_topics_parser.add_mutually_exclusive_group(required=True)
    atp_meg.add_argument(
        '-c', '--with-ci-required', dest='with_ci_required',
        action='store_true',
        help='repos that have been identified as requiring CI will\n'
             'be tagged with the specified GitHub topic(s)\n'
             f'[default topic: "{DEFAULT_TOPIC_NAME_SINGLE_REPORT}"]')
    atp_meg.add_argument(
        '-m', '--with-missing-topics', dest='with_missing_topics',
        action='store_true',
        help=f'repos that do not have any of {TOPICS_WANTED}\nwill be tagged '
             f'with "{TOPIC_UNKNOWN}"\n(specified topics are ignored)')
    atp_meg.add_argument(
        '-r', '--repos', dest='the_repos', nargs='+', metavar='<the-repo>',
        help='the specified topic(s) will be added to the repos')
    atp_meg.add_argument(
        '-t', '--topic', dest='the_topic', metavar='<existing-topic>',
        help='find the repos that have the <existing-topic> and apply '
             'the specified topic(s) to the matching repos')
    apply_topics_parser.set_defaults(func=do_apply_topics)
    apply_topics_parser._positionals.title = 'optional arguments'
    #----------------------------------------------------------------------------------------
    desc = 'Create CSV file with this info:\n ' \
           'repo name, repo URL, developer, manager, director, ' \
           'primary language, other languages, topics '
    create_report_parser = subparsers.add_parser(
        'create-report', aliases=['cr'],
        epilog=f'Examples:{__help_create_report}',
        help='create CSV file',
        description=desc,
        formatter_class=RawTextHelpFormatter,
        parents=[common_optional_parser])
    create_report_parser.add_argument(
        'wanted_topic_names', nargs='*', metavar='<topic-names>',
        help='include repos with any of the <topic-names>\n')
    cpp_meg = create_report_parser.add_mutually_exclusive_group(required=True)
    cpp_meg.add_argument(
        '-e', '--each-manager', dest='report_for_mgr',
        action='store_true',
        help='create a CSV file for each second line manager')
    cpp_meg.add_argument(
        '-c', '--with-ci', dest='ci_report',
        # '-s', '--single-report', dest='single_report',
        action='store_true',
        help='create a CSV file for repos with CI')
    cpp_meg.add_argument(
        '-m', '--master-report', dest='master_report',
        action='store_true',
        help='create the master CSV file containing repos with any '
             'topic\n(specified topics are ignored)')
    create_report_parser.set_defaults(func=do_create_report)
    create_report_parser._positionals.title = 'optional arguments'
    #----------------------------------------------------------------------------------------
    desc = 'Email CSV file'
    email_report_parser = subparsers.add_parser(
        'email-report', aliases=['er'],
        epilog=f'Examples:{__help_email_report}',
        help='email CSV file',
        description=desc,
        formatter_class=RawTextHelpFormatter,
        parents=[common_optional_parser])
    erp_meg = email_report_parser.add_mutually_exclusive_group(required=True)
    erp_meg.add_argument(
        '-e', '--each-manager', dest='email_report_for_mgr',
        action='store_true',
        help='send email to each second line manager and attach CSV file')
    erp_meg.add_argument(
        '-u', '--unknown-repos', dest='email_unknown_repos',
        action='store_true',
        help=f'send email to each developer and first line manager about '
             f'repos\nthat have only "{TOPIC_UNKNOWN}" GitHub topic, and '
             f'does NOT have "{TOPIC_FOR_PROD}"\nor "{TOPIC_NOT_FOR_PROD}" ')
    email_report_parser.set_defaults(func=do_email_report)
    email_report_parser._positionals.title = 'optional arguments'
    #----------------------------------------------------------------------------------------
    desc = 'Remove the GitHub topic(s) from the GitHub repos'
    remove_topics_parser = subparsers.add_parser(
        'remove-topics', aliases=['rt'],
        epilog=f'Examples:{__help_remove_topics}',
        help='remove GitHub topics',
        description=desc,
        formatter_class=RawTextHelpFormatter,
        parents=[common_optional_parser])
    remove_topics_parser.add_argument(
        'wanted_topic_names', nargs='*', metavar='<topic-name>',
        help='the topic name(s) to be removed')
    rtp_meg = remove_topics_parser.add_mutually_exclusive_group(required=True)
    rtp_meg.add_argument(
        '-l', '--with-lower-priority', dest='with_lower_priority',
        action='store_true',
        help='Repos with multiple GitHub topics (glcp-production, '
             'glcp-not-production, glcp-unknown)\n'
             'will be updated and the lower priority topics will be removed.\n'
             '"glcp-production" has the highest priority and "glcp-unknown"\n'
             'has the lowest priority (specified topics are ignored)')
    rtp_meg.add_argument(
        '-r', '--repos', dest='the_repos', nargs='+', metavar='<the-repo>',
        help='the specified topic(s) will be removed from the specified repo(s)')
    remove_topics_parser.set_defaults(func=do_remove_topics)
    remove_topics_parser._positionals.title = 'optional arguments'
    #----------------------------------------------------------------------------------------

    # The "dest='subcmd'" assignment during the creation of the subparsers
    # makes the help screen show:
    #    repo-ops: error: the following arguments are required: subcmd

    # To restore the original behavior (when dest='....' is not used) so that help screen shows:
    #    repo-ops: error: the following arguments are required: {.....}

    # the metavar needs to be reset:
    # https://stackoverflow.com/questions/45006963/get-subparser-by-name
    subparsers.metavar = '{' + ','.join(subparsers.choices) + '}'
    # ----------------------------------------------------------------------------------------

    args = main_parser.parse_args()
    if args.verbose_level == 'none':
        args.verbose_level = 'notset'

    # for the "apply-topics" subcmd
    if attr_val(args, 'with_missing_topics'):
        # print('here 1 with_missing_topics')
        args.wanted_topic_names = TOPICS_WANTED
    elif attr_val(args, 'with_ci_required') and not args.wanted_topic_names:
        # print('here 2 with_ci_required')
        args.wanted_topic_names = [DEFAULT_TOPIC_NAME_SINGLE_REPORT]
    elif attr_val(args, 'the_repos') and not args.wanted_topic_names:
        # print('here 3 the_repos')
        print(f'ERROR: need to specify the topic(s)')
        sys.exit(1)
    elif attr_val(args, 'the_topic') and not args.wanted_topic_names:
        # print('here 4 the_topic')
        print(f'ERROR: need to specify the topic(s)')
        sys.exit(1)

    # for the "create-report" subcmd
    elif attr_val(args, 'report_for_mgr') and not args.wanted_topic_names:
        # print('here 5 report_for_mgr')
        args.wanted_topic_names = TOPICS_WANTED
    elif attr_val(args, 'ci_report') and not args.wanted_topic_names:
        # print('here 6 ci_report')
        args.wanted_topic_names = [DEFAULT_TOPIC_NAME_SINGLE_REPORT]

    return args


def attr_val(obj: object, attr: str) -> bool:
    """
    Return true if the object has the specified attribute AND
    the attribute evaluates to True.
    """

    return hasattr(obj, attr) and getattr(obj, attr)


main()

