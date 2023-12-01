#!/usr/bin/env python3
import re

diff_file = 'git-diff.txt/git-diff.txt'
lint_logfile = 'super-linter.log'
file_lines_dict = {}
words = ['^diff', '^\+\s+', '^\-\s+', '^\+\d+', '^\-\d+']
dictname = ''

def line_starts_with_any_word(line, words):
    return any(re.match(word, line) for word in words)

with open(diff_file, 'r') as file:
    for line_number, line in enumerate(file, start=1):
        line = line.strip()
        if line_starts_with_any_word(line, words):
            if line.startswith('diff'):
                dictname = (line.split(" ")[-1])[2:]
                cleaned_line = dictname.strip().replace('/github/workspace/', '')
                if dictname not in file_lines_dict.keys():
                    file_lines_dict[dictname] = []
            else:
                line_num = re.findall(r'\b\d+\b', line)
                for ln in line_num:
                    file_lines_dict[dictname].append(ln)

error_dict = {key: [] for key in file_lines_dict}

with open(lint_logfile, 'r') as file:
    for line in file:
        if "line " in line:
            match = re.search(r'In (.+) line (\d+):', line)
            if match:
                file_path = match.group(1)
                line_number = match.group(2)
                file_path = file_path.strip().replace('/github/workspace/', '')
                if file_path in error_dict:
                    if line_number not in error_dict[file_path]:
                        error_dict[file_path].append(line_number)
                else:
                    error_dict[file_path] = [line_number]

for file, errors in error_dict.items():
    if errors:
        error_message = f"{file} having lint error on line {', '.join(errors)}"
        print(error_message)