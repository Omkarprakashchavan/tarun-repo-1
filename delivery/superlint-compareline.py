import re
import sys

diff_file = 'git-diff.txt'
lint_logfile = 'super-linter.log'
file_lines_dict = {}
words = ['^diff', '^\+\s+', '^\-\s+', '^\+\d+', '^\-\d+', '^\-+', '^\++']
dictname = ''

def line_starts_with_any_word(line, words):
    return any(re.match(word, line) for word in words)

with open(diff_file, 'r', encoding="utf8") as file:
    for line_number, line in enumerate(file, start=1):
        line = line.strip()
        if line_starts_with_any_word(line, words):
            if line.startswith('diff'):
                dictname = (line.split(" ")[-1])[2:]
                cleaned_line = dictname.strip().replace('/github/workspace/', '')
                if dictname not in file_lines_dict.keys():
                    file_lines_dict[dictname] = []
            else:
                line_num = re.findall(r'^[+-]\s*(\d+)', line)
                for ln in line_num:
                    file_lines_dict[dictname].append(ln)


print(f'Printing Git-Diff output ----------------')
f = open(diff_file, "r")
print(f.read())
print(f'closing Git-Diff output ----------------')
error_dict = {key: [] for key in file_lines_dict}
print(f'Before lint output {error_dict}')
unique_file_lines_dict = {key: list(set(value)) for key, value in file_lines_dict.items()}
line_num_pattern = r'\b\d+:'#\d+\b'

with open(lint_logfile, 'r', encoding="utf8") as file:
    for line in file:
        if re.search('error', line, re.IGNORECASE):
            # print(line)
            line_list = line.split(' ')
            filename = [word for word in line_list if '/' in word]
            if len(filename) > 0:
                # print(filename)
                filename = str(filename).strip().replace('/github/workspace/', '').replace('[', '').replace("'",'').replace(']','')
                error_filename = filename.split(':')[0]
                # print(error_filename)
                for word in line_list:
                    # print(word)
                    result = None
                    match = re.search(line_num_pattern, word)
                    if match:
                        result = match.group().split(':')[0]
                        print(filename, error_filename, match, result)
                        break
                if error_filename in error_dict:
                    if result not in error_dict[error_filename]:
                        error_dict[error_filename].append(result)
        if "line " in line:
            match = re.search(r'In (.+) line (\d+):', line)
            if match:
                file_path = match.group(1)
                line_number = match.group(2)
                file_path = file_path.strip().replace('/github/workspace/', '')
                if file_path in error_dict:
                    if line_number not in error_dict[file_path]:
                        error_dict[file_path].append(line_number)
        if line.startswith('  on '):
            line_data = line.split(' ')
            error_filename = line_data[3]
            error_line = line_data[5][:-1]
            print(line, error_filename, error_line)
            if error_filename in error_dict:
                if error_line not in error_dict[error_filename]:
                    error_dict[error_filename].append(error_line)

error_dict = {key: list(set(value)) for key, value in error_dict.items()}
print(f'After lint output {error_dict}')
output_dict = {}
for key in error_dict.keys():
    if key in file_lines_dict:
        common_values = set(error_dict[key]) & set(file_lines_dict[key])
        if common_values:
            output_dict[key] = common_values
            print(f"'{key} having linting error on line': {common_values}")

print(output_dict)
any_non_empty = any(errors for errors in output_dict.values())
if any_non_empty:
    sys.exit(1)
