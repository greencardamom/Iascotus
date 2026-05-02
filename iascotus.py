#!/usr/bin/env python3
import sys
import subprocess
import re
import json
import urllib.request
import urllib.parse
import time
import argparse

#
# GitHub: https://github.com/Greencardamom/Iascotus
#

DEBUG = False

def print_verbose(msg):
    """Prints debug information to stdout if dry-run mode is active."""
    if DEBUG:
        print(f"[DEBUG] {msg}")

def extract_case_param(template_text):
    """
    Safely extracts the |case= parameter value.
    Uses brace and bracket balancing to ignore pipes inside nested {{ }} or [[ ]].
    """
    m = re.search(r'\|\s*case\s*=', template_text, re.IGNORECASE)
    if not m:
        return None
        
    start_idx = m.end()
    brace_level = 0
    bracket_level = 0
    i = start_idx
    
    while i < len(template_text):
        if template_text[i:i+2] == '{{':
            brace_level += 1
            i += 2
            continue
        elif template_text[i:i+2] == '}}':
            if brace_level > 0:
                brace_level -= 1
                i += 2
                continue
            else:
                # Reached the closing braces of the parent {{caselaw source}} template
                return template_text[start_idx:i].strip()
        elif template_text[i:i+2] == '[[':
            bracket_level += 1
            i += 2
            continue
        elif template_text[i:i+2] == ']]':
            if bracket_level > 0:
                bracket_level -= 1
            i += 2
            continue
        elif template_text[i] == '|' and brace_level == 0 and bracket_level == 0:
            # Top-level pipe! This means the next parameter is starting.
            return template_text[start_idx:i].strip()
            
        i += 1
        
    return template_text[start_idx:].strip()

def insert_internetarchive(template_text, ia_result):
    """
    Dynamically inserts the |internetarchive= parameter.
    Captures local whitespace bridges to perfectly emulate hybrid inline/multiline templates.
    """
    brace_level = 0
    bracket_level = 0
    params = []
    
    pad_pipe = ""
    pad_eq_left = ""
    pad_eq_right = ""
    
    i = 0
    while i < len(template_text):
        if template_text[i:i+2] == '{{':
            brace_level += 1; i += 2; continue
        elif template_text[i:i+2] == '}}':
            brace_level -= 1; i += 2; continue
        elif template_text[i:i+2] == '[[':
            bracket_level += 1; i += 2; continue
        elif template_text[i:i+2] == ']]':
            bracket_level -= 1; i += 2; continue
        
        elif template_text[i] == '|' and brace_level == 1 and bracket_level == 0:
            m = re.match(r'\|([ \t]*)([a-zA-Z0-9_]+)([ \t]*)=([ \t]*)', template_text[i:])
            if m:
                # Capture spacing style, capping at 1 to prevent huge alignment gaps
                pad_pipe = m.group(1) if len(m.group(1)) <= 1 else " "
                pad_eq_left = m.group(3) if len(m.group(3)) <= 1 else " "
                pad_eq_right = m.group(4) if len(m.group(4)) <= 1 else " "
                
                # Walk backwards to capture the exact whitespace preceding this parameter
                start_ws = i - 1
                while start_ws >= 0 and template_text[start_ws] in [' ', '\t', '\n', '\r']:
                    start_ws -= 1
                ws_string = template_text[start_ws+1:i]
                        
                params.append({
                    'name': m.group(2).lower(),
                    'i': i,
                    'ws_string': ws_string
                })
        i += 1

    repo_params = [p for p in params if p['name'] != 'case']
    repo_names = [p['name'] for p in repo_params]
    is_sorted = (repo_names == sorted(repo_names)) and len(repo_names) > 0
    
    insert_target = None

    if is_sorted:
        for p in repo_params:
            if p['name'] > "internetarchive":
                insert_target = p
                break

    if insert_target:
        # Insert before the target parameter, using the target's preceding whitespace as the bridge
        styled_param = f"|{pad_pipe}internetarchive{pad_eq_left}={pad_eq_right}{ia_result}{insert_target['ws_string']}"
        return template_text[:insert_target['i']] + styled_param + template_text[insert_target['i']:]
    else:
        # Append to the end, using the final parameter's preceding whitespace as the formatting guide
        last_ws = params[-1]['ws_string'] if params else " "
        if not last_ws: 
            last_ws = " "
            
        # Use regex to safely remove only the final closing braces of the parent template,
        # avoiding the accidental destruction of nested template braces at the end of the line.
        clean_template = re.sub(r'\s*\}\}\s*$', '', template_text)    
        styled_param = f"|{pad_pipe}internetarchive{pad_eq_left}={pad_eq_right}{ia_result}"
        
        # Determine if the template should close on a new line or inline
        close_brace = "\n}}" if '\n' in last_ws else "}}"
        
        return f"{clean_template}{last_ws}{styled_param}{close_brace}"

def parse_case_string(case_str, template_text=""):
    """
    Extracts and normalizes the SCOTUS citation and case name.
    Mines external URLs for docket numbers on recent/slip opinions.
    """
    clean_str = re.sub(r"''+", "", case_str).strip()
    print_verbose(f"Parsing raw |case= string: '{clean_str}'")
    
    params = {}
    
    # Scan the entire template for docket numbers hidden in external URLs
    if template_text:
        m_url = re.search(r'(?:justia\.com|oyez\.org|supremecourt\.gov|cornell\.edu).*?(?:/|=)(\d{2}-\d{3,4})\b', template_text, re.IGNORECASE)
        if m_url:
            print_verbose(f"Mined Docket No from template URLs: {m_url.group(1)}")
            params["docket"] = m_url.group(1)
            
    # 1. Extract the case name for fallback API queries
    if "name=" in clean_str.lower():
        m = re.search(r'name\s*=\s*([^|}]+)', clean_str, re.IGNORECASE)
        if m: params['name'] = m.group(1).strip()
    else:
        m = re.match(r'^([^,{]+)', clean_str)
        if m: params['name'] = m.group(1).strip()
            
    # 2. Match nested {{ussc|...}} templates
    ussc_match = re.search(r'\{\{\s*ussc\s*\|(.*?)\}\}', clean_str, re.IGNORECASE)
    if ussc_match:
        print_verbose(f"Matched {{ussc}} template: {ussc_match.group(0)}")
        ussc_params = ussc_match.group(1).split('|')
        
        named_vol = None
        named_page = None
        
        for p in ussc_params:
            p_lower = p.lower()
            if 'docket' in p_lower and '=' in p:
                docket_val = p.split('=', 1)[1].strip()
                m_dock = re.search(r'(\d{2}-\d{3,4})', docket_val)
                if m_dock:
                    print_verbose(f"Extracted USSC docket: {m_dock.group(1)}")
                    params["docket"] = m_dock.group(1)
                    return params
            elif 'volume' in p_lower and '=' in p:
                named_vol = p.split('=', 1)[1].strip()
            elif 'page' in p_lower and '=' in p:
                named_page = p.split('=', 1)[1].strip()

        # Check if explicitly named volume and page were found
        if named_vol and named_page:
            if named_vol.isdigit() and (named_page.isdigit() or '_' in named_page):
                print_verbose(f"Extracted USSC named parameters: {named_vol} U.S. {named_page}")
                params["us"] = f"{named_vol} U.S. {named_page}"
                return params
                
        # Fallback to positional extraction (e.g., 113|40)
        positional = [p.strip() for p in ussc_params if '=' not in p]
        if len(positional) >= 2:
            vol = positional[0]
            page = positional[1]
            if vol.isdigit() and (page.isdigit() or '_' in page):
                print_verbose(f"Extracted USSC positional: {vol} U.S. {page}")
                params["us"] = f"{vol} U.S. {page}"
                return params

    # 3. Match standard U.S. citation
    m = re.search(r'\b(\d+)\s+[Uu]\.?\s*[Ss]\.?\s+(\d+)\b', clean_str)
    if m:
        print_verbose(f"Matched official U.S. citation: {m.group(1)} U.S. {m.group(2)}")
        params["us"] = f"{m.group(1)} U.S. {m.group(2)}"
        return params
        
    # 4. Match S. Ct. citation
    m = re.search(r'\b(\d+)\s+[Ss]\.?\s*[Cc]t\.?\s+(\d+)\b', clean_str)
    if m:
        print_verbose(f"Matched S. Ct. citation: {m.group(1)} S. Ct. {m.group(2)}")
        params["sct"] = f"{m.group(1)} S. Ct. {m.group(2)}"
        return params
        
    # 5. Match Docket No. 
    m = re.search(r'\b(\d{2}[-–]\d{3,4})\b', clean_str)
    if m:
        # Normalize en-dash to standard hyphen for the API
        clean_docket = m.group(1).replace('–', '-')
        print_verbose(f"Matched Docket No: {clean_docket}")
        params["docket"] = clean_docket
        return params
        
    # 6. The Hard Gatekeeper: Must have a strict SCOTUS identifier.
    # We explicitly exclude "name" here to block lower/state court cases.
    if "docket" in params or "us" in params or "sct" in params:
        return params

    print_verbose("No valid SCOTUS citation or docket found.")
    return None

def validate_ia_scotus(query_params):
    """
    Queries the Internet Archive API returning JSON. 
    Uses a strict citation search first, with a fallback to a literal case name search.
    """
    def query_api(query_str):
        url = f"https://archive.org/advancedsearch.php?q={urllib.parse.quote(query_str)}&fl[]=identifier&output=json&rows=5"
        req = urllib.request.Request(url, headers={'User-Agent': 'WikipediaBot/1.0 (GreenC)'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data['response']['numFound'], data['response'].get('docs', []), url

    hit_count = 0
    url1 = ""

    # --- Stage 1: Strict Citation Search ---
    # Guard: ONLY run Stage 1 if we have a strict identifier
    if "docket" in query_params or "us" in query_params or "sct" in query_params:
        lucene_query = "collection:(us-supreme-court)"
        param_key, param_val = "", ""
        
        if "docket" in query_params:
            lucene_query += f' AND title:"{query_params["docket"]}"'
            param_key, param_val = "docket", query_params["docket"]
        elif "us" in query_params:
            lucene_query += f' AND title:"{query_params["us"]}"'
            param_key, param_val = "us", query_params["us"]
        elif "sct" in query_params:
            lucene_query += f' AND title:"{query_params["sct"]}"'
            param_key, param_val = "sct", query_params["sct"]

        try:
            hit_count, docs, url1 = query_api(lucene_query)
            print_verbose(f"IA API returned {hit_count} hits for citation search.")
            
            if hit_count == 1:
                doc_id = docs[0]['identifier']
                print_verbose(f"Success: Exact match found. ID: {doc_id}")
                return f"{{{{IA SCOTUS URL |id={doc_id}}}}}", f"ID: {doc_id}", "1 item found", url1
                
            elif hit_count == 2:
                print_verbose("Success: 2 matches found. Falling back to dynamic search template.")
                return f"{{{{IA SCOTUS URL |{param_key}={param_val}}}}}", "items 2", "2 items found", url1
                
            elif hit_count > 2:
                # If a strict citation hits massive numbers, it's a metadata failure. Skip.
                return f"SKIP: returned {hit_count} results", f"items {hit_count}", f"{hit_count} items found", url1
                
        except Exception as e:
            print_verbose(f"API Request Error (Stage 1): {str(e)}")
            return f"ERROR: API request failed - {str(e)}", "API Error", "API Error", "URL generation error"

    # --- Stage 2: Fallback Literal Name Search ---
    # Trigger if Stage 1 found nothing (0 hits) OR if Stage 1 was bypassed completely
    if hit_count == 0 and "name" in query_params:
        print_verbose(f"Strict search failed or bypassed. Trying fallback literal case name: {query_params['name']}")
        try:
            fallback_query = f'collection:(us-supreme-court) AND title:"{query_params["name"]}"'
            hit_count_2, docs_2, url2 = query_api(fallback_query)
            print_verbose(f"IA API returned {hit_count_2} hits for fallback search.")
            
            if hit_count_2 >= 1:
                print_verbose(f"Golden Ticket: Found {hit_count_2} related docs. Using dynamic search link.")
                # Inject the literal string match into the new Wikipedia template parameter
                search_val = f'title:"{query_params["name"]}"'
                return f"{{{{IA SCOTUS URL |search={search_val}}}}}", f"items {hit_count_2}", f"{hit_count_2} items found via search", url2
            else:
                return f"SKIP: returned 0 results", "items 0", "0 items found", url2
                
        except Exception as e:
            print_verbose(f"API Request Error (Stage 2): {str(e)}")
            return f"ERROR: API request failed - {str(e)}", "API Error", "API Error", "URL generation error"

    # Safety Net
    return f"SKIP: returned 0 results", "items 0", "0 items found", ""

def get_closing_brace(text, start_idx):
    """Robust parser to find the exact closing braces of a wikitext template."""
    brace_count = 0
    i = start_idx
    while i < len(text) - 1:
        if text[i:i+2] == '{{':
            brace_count += 1
            i += 2
            continue
        elif text[i:i+2] == '}}':
            brace_count -= 1
            if brace_count == 0:
                return i + 2
            i += 2
            continue
        i += 1
    return -1

def write_log(logfile, title, old_template, info, extra_col=None):
    """Formats and writes a single-line log entry, with an optional 4th column."""
    old_flat = old_template.replace("\n", "__NEWLINE__")
    info_flat = info.replace("\n", "__NEWLINE__")
    
    line = f"{title} ---- {old_flat} ---- {info_flat}"
    if extra_col:
        line += f" ---- {extra_col}"
        
    with open(logfile, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def main():
    global DEBUG
    
    parser = argparse.ArgumentParser(description="Populate IA SCOTUS links in Caselaw source templates.")
    parser.add_argument('-t', '--title', help="Process a single Wikipedia article title")
    parser.add_argument('-f', '--file', help="File containing list of titles")
    parser.add_argument('-d', '--debug', action='store_true', help="Enable verbose debug output")
    parser.add_argument('-l', '--live', action='store_true', help="Enable LIVE upload mode (default is dry-run)")

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args = parser.parse_args()
    DEBUG = args.debug
    LIVE_MODE = args.live

    if not args.title and not args.file:
        parser.error("You must specify either a single title (-t) or an input file (-f).")

    if args.title:
        titles = [args.title]
        print_verbose(f"Single title mode active: '{args.title}'")
    else:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                titles = [line.strip() for line in f if line.strip()]
            print_verbose(f"Loaded {len(titles)} titles from {args.file}")
        except FileNotFoundError:
            print(f"Error: {args.file} not found.")
            sys.exit(1)

    template_start_regex = re.compile(r'\{\{\s*(?:[Tt]emplate:)?\s*[Cc]aselaw source\b', re.IGNORECASE)

    for title in titles:
        print_verbose(f"\n{'='*50}\nProcessing Article: {title}\n{'='*50}")
        time.sleep(1)
        
        article_status = "No caselaw source templates found"
        
        print_verbose("Fetching wikitext via wikiget.awk...")
        res = subprocess.run(['wikiget.awk', '-w', title], capture_output=True, text=True)
        if res.returncode != 0 or not res.stdout.strip():
            print_verbose("Failed to fetch wikitext.")
            write_log("ia_scotus_error.log", title, "N/A", "wikiget failed to fetch wikitext")
            article_status = "wikiget failed to fetch wikitext"
            sys.stderr.write(f"{title} ---- {article_status}\n")
            continue
            
        wikitext = res.stdout
        new_wikitext = wikitext
        modified = False
        
        search_idx = 0
        while True:
            match = template_start_regex.search(new_wikitext, search_idx)
            if not match:
                break
                
            start_idx = match.start()
            end_idx = get_closing_brace(new_wikitext, start_idx)
            
            if end_idx == -1:
                print_verbose("Malformed template braces detected. Aborting template search.")
                write_log("ia_scotus_error.log", title, "N/A", "Malformed template braces detected")
                if not modified: article_status = "Malformed template braces detected"
                break
                
            template_text = new_wikitext[start_idx:end_idx]
            search_idx = end_idx
            
            print_verbose(f"Found template:\n{template_text}")
            
            if re.search(r'\|\s*(internetarchive|archiveurl)\s*=', template_text, re.IGNORECASE):
                print_verbose("Template already contains an archive link. Skipping.")
                write_log("ia_scotus_error.log", title, template_text, "Template already contains an archive link")
                if not modified: article_status = "Template already contains an archive link"
                continue
                
            case_str = extract_case_param(template_text)
            
            if not case_str:
                print_verbose("Missing or unparseable |case= parameter. Skipping.")
                write_log("ia_scotus_error.log", title, template_text, "Missing or unparseable |case= parameter")
                if not modified: article_status = "Missing or unparseable |case= parameter"
                continue
                
            query_params = parse_case_string(case_str, template_text)    
            if not query_params:
                write_log("ia_scotus_error.log", title, template_text, "Filtered: No SCOTUS citation format found")
                if not modified: article_status = "Filtered: No SCOTUS metadata found"
                continue
                
            # Unpack the 4 variables from our updated function
            ia_result, extra_col, api_status, query_url = validate_ia_scotus(query_params)
            
            if ia_result.startswith("SKIP") or ia_result.startswith("ERROR"):
                print_verbose(f"Validation failed: {ia_result}")
                write_log("ia_scotus_error.log", title, template_text, f"{ia_result} ---- {query_url}")
                if not modified: article_status = api_status
                continue
                
            print_verbose("Generating updated wikitext block...")
            new_template_text = insert_internetarchive(template_text, ia_result)
            new_wikitext = new_wikitext[:start_idx] + new_template_text + new_wikitext[end_idx:]
            
            search_idx = start_idx + len(new_template_text)
            modified = True
            
            # Pass the 4th column specifically to the upload log
            write_log("ia_scotus_upload.log", title, template_text, new_template_text, extra_col)

        if modified:
            if not LIVE_MODE:
                print_verbose(f"DRY RUN: Modifications completed for '{title}'. Skipping upload step. Use -l to execute.")
                article_status = "Dry-run: generated an entry"
            else:
                print_verbose(f"Uploading changes for '{title}'...")
                upload_proc = subprocess.run(
                    ['wikiget.awk', '-E', title, '-S', "Adding IA SCOTUS link", '-P', 'STDIN'],
                    input=new_wikitext,
                    text=True,
                    capture_output=True
                )
                
                if upload_proc.returncode != 0:
                    print_verbose(f"Upload failed: {upload_proc.stderr.strip()}")
                    write_log("ia_scotus_error.log", title, "N/A", f"Upload failed: {upload_proc.stderr.strip()}")
                    article_status = "Upload failed"
                else:
                    print_verbose("Upload successful.")
                    article_status = "Uploaded an entry"
        
        # Print the final 1-line progress update to stderr
        sys.stderr.write(f"{title} ---- {article_status}\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        if DEBUG:
            print("\n[DEBUG] Script interrupted by user. Exiting gracefully.")
        sys.exit(0)
