# iascotus

A command-line utility to populate `|internetarchive=` parameter in Wikipedia's `{{Caselaw source}}` templates. It parses citations, queries the Internet Archive's Advanced Search API for US Supreme Court metadata, and safely edits wikitext using `wikiget.awk`.

## Requirements

* Python 3
* [`wikiget.awk`](https://github.com/greencardamom/Wikiget) configured with OAuth-Consumer keys and an account with bot-flag permissions.

## Usage

The script defaults to a **dry-run** mode to prevent accidental uploads. You must explicitly pass the `-l` flag to write changes to Wikipedia.

```text
usage: iascotus.py [-h] [-t TITLE] [-f FILE] [-d] [-l]

options:
  -h, --help            show this help message and exit
  -t TITLE, --title TITLE
                        Process a single Wikipedia article title
  -f FILE, --file FILE  File containing list of titles
  -d, --debug           Enable verbose debug output to terminal
  -l, --live            Enable LIVE upload mode (default is dry-run)
```

## Operation

* Download and configure `wikiget.awk`.  
* Download article-titles to process
* * `wikiget -b "Template:Caselaw source" -tt > titles.txt`
* Test then run the bot per examples below

## Examples

**Dry-run a single article with verbose output (Safe testing):**
```tcsh
./iascotus.py -t "Healy v. James" -d
```

**Live edit a single article:**
```tcsh
./iascotus.py -t "Healy v. James" -l
```

**Process a batch of articles silently (Dry-run):**
```tcsh
./iascotus.py -f titles.txt
```

**Process a batch of articles (Live edit):**
```tcsh
./iascotus.py -f titles.txt -l
```

## Logging

The script automatically generates two local log files in the working directory:
* `ia_scotus_upload.log`: Records successful template modifications.
* `ia_scotus_error.log`: Records skipped templates, missing metadata, and API or upload failures.
