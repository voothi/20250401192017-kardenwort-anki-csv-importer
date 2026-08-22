#!/usr/bin/env python3

import argparse
import csv
import requests
import os
import tempfile
import sys
import json
import re
import datetime
from pathlib import Path

ANKI_CONNECT_URL = os.environ.get('ANKI_CONNECT_URL', 'http://127.0.0.1:8765')


def write_import_log_entry(log_path, trace_id, level, message):
    if not log_path:
        return
    try:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{now_str}] [{trace_id}] [{level}] {message}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def emit_error_and_exit(code, message, details=None, zid="", trace_id="", log_file=None):
    payload = {
        "status": "error",
        "zid": zid,
        "trace_id": trace_id or (f"{zid}:export:anki" if zid else "export:anki"),
        "code": code,
        "message": message,
        "details": details or {}
    }
    if log_file:
        write_import_log_entry(log_file, payload["trace_id"], "ERROR", f"[{code}] {message}")
    print(json.dumps(payload))
    print(f"[E] {message}", file=sys.stderr)
    sys.exit(1)


def probe_ankiconnect(url=ANKI_CONNECT_URL, timeout=2.0):
    try:
        r = requests.post(url, json={'action': 'version', 'version': 6}, timeout=timeout)
        if r.status_code == 200:
            res = r.json()
            if res.get('error') is None and 'result' in res:
                return True, None
            return False, res.get('error', 'AnkiConnect returned an error')
        return False, f"HTTP status code {r.status_code}"
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def parse_ac_response(response):
    if len(response) != 2:
        raise Exception('response has an unexpected number of fields')
    if 'error' not in response:
        raise Exception('response is missing required error field')
    if 'result' not in response:
        raise Exception('response is missing required result field')
    if response['error'] is not None:
        raise Exception(response['error'])
    return response['result']


def make_ac_request(action, **params):
    return {'action': action, 'params': params, 'version': 6}


def invoke_ac(action, **params):
    requestJson = make_ac_request(action, **params)
    try:
        response = requests.post(ANKI_CONNECT_URL, json=requestJson).json()
    except requests.exceptions.ConnectionError:
        print('[E] Failed to connect to AnkiConnect, make sure Anki is running', file=sys.stderr)
        exit(1)

    return parse_ac_response(response)


def invoke_multi_ac(multi_actions):
    multi_results = invoke_ac('multi', actions=multi_actions)
    results = []
    for res in multi_results:
        results.append(parse_ac_response(res))
    return results


def tsv_to_ac_notes(tsv_path, deck_name, note_type):
    notes = []
    index_to_field_name = {}
    with open(tsv_path, encoding='utf-8') as tsvfile:
        reader = csv.reader(tsvfile, delimiter='\t')
        header = None
        has_deck_column = False
        
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            if row[0].startswith('#'):
                continue
            if header is None:
                header = row
                for j, field_name in enumerate(header):
                    index_to_field_name[j] = field_name
                has_deck_column = 'Deck' in header
                if not deck_name and not has_deck_column:
                    raise ValueError("[E] --deck is required when no 'Deck' column is present in the file")
                continue

            fields = {}
            tags = []
            current_deck = deck_name

            for j, field_value in enumerate(row):
                if j not in index_to_field_name:
                    continue
                
                field_name = index_to_field_name[j]
                
                if has_deck_column and field_name == 'Deck' and field_value:
                    current_deck = field_value
                
                if field_name.lower() == 'tags':
                    tags = field_value.split(' ') if field_value else []
                else:
                    fields[field_name] = field_value

            if not current_deck:
                raise ValueError("Error: No deck name found for a row. Provide a deck via --deck argument or a 'Deck' column in the file.")

            note = {
                'deckName': current_deck,
                'modelName': note_type,
                'fields': fields,
                'tags': tags,
                'options': {
                    "allowDuplicate": True,
                    "duplicateScope": "deck"
                }
            }
            notes.append(note)

    return notes


def get_ac_add_and_update_note_lists(notes):
    result = invoke_ac('canAddNotes', notes=notes)

    notes_to_add = []
    notes_to_update = []
    for i, b in enumerate(result):
        if b:
            notes_to_add.append(notes[i])
        else:
            notes_to_update.append(notes[i])

    return notes_to_add, notes_to_update


def ac_update_notes_and_get_note_info(notes_to_update, find_note_results):
    actions = []
    for i, n in enumerate(notes_to_update):
        unique_field_name = 'Quotation' if 'Quotation' in n['fields'] else 'Front'
        unique_field_value = n['fields'].get(unique_field_name, '')

        find_note_result = find_note_results[i]
        if len(find_note_result) == 0:
            print('[W] Did not find any results for note with {} "{}", '
                  'skipping. This is likely a bug, '
                  'please report this to the developer'.format(unique_field_name, unique_field_value), file=sys.stderr)
            continue
        elif len(find_note_result) > 1:
            print('[W] Duplicate notes are not supported, '
                  'skipping note with {} "{}"'.format(unique_field_name, unique_field_value), file=sys.stderr)
            continue

        n['id'] = find_note_result[0]
        actions.append(make_ac_request('updateNoteFields', note=n))

        actions.append(make_ac_request('notesInfo', notes=[n['id']]))
        if n['tags']:
            actions.append(
                make_ac_request(
                    'addTags',
                    notes=[n['id']],
                    tags=' '.join(n['tags'])))

    note_info_results = [res for res in invoke_multi_ac(actions) if res is not None]

    new_notes_to_update = [n for n in notes_to_update if 'id' in n]

    assert len(note_info_results) == len(new_notes_to_update)
    return new_notes_to_update, note_info_results


def ac_remove_tags(notes_to_update, note_info_results):
    remove_tags_actions = []
    for i, n in enumerate(notes_to_update):
        note_info_result = note_info_results[i]
        assert(len(note_info_result) == 1)

        existing_tags = note_info_result[0]['tags']
        tags_to_remove = list(set(existing_tags) - set(n['tags'] if n['tags'] else []))

        if tags_to_remove:
            remove_tags_actions.append(
                make_ac_request(
                    'removeTags',
                    notes=[n['id']],
                    tags=' '.join(tags_to_remove)))
    if remove_tags_actions:
        invoke_multi_ac(remove_tags_actions)

def update_deck_descriptions(metadata_file_path):
    if not metadata_file_path or not os.path.exists(metadata_file_path):
        return
    
    print(f"[+] Processing deck metadata from {metadata_file_path}...", file=sys.stderr)
    try:
        with open(metadata_file_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"[W] Could not read or parse metadata file: {e}", file=sys.stderr)
        return

    descriptions = metadata.get("deck_descriptions")
    if not isinstance(descriptions, dict):
        return
        
    for deck_name, description in descriptions.items():
        try:
            print(f"    - Updating description for deck: '{deck_name}'", file=sys.stderr)
            invoke_ac('setDeckDescription', deck=deck_name, description=description)
        except Exception as e:
            print(f"[W] Failed to update description for deck '{deck_name}': {e}", file=sys.stderr)

def send_to_anki_connect(tsv_path, deck_name, note_type, suspend_cards, metadata_file_path):
    BATCH_SIZE = 100
    notes = tsv_to_ac_notes(tsv_path, deck_name, note_type)

    all_deck_names = sorted(list(set(note['deckName'] for note in notes)))
    print(f"[+] Found {len(all_deck_names)} unique decks. Ensuring they exist...", file=sys.stderr)
    
    if all_deck_names:
        create_deck_actions = [make_ac_request('createDeck', deck=deck) for deck in all_deck_names]
        invoke_multi_ac(create_deck_actions)
    
    update_deck_descriptions(metadata_file_path)

    total_notes = len(notes)
    print(f"[+] Starting to process {total_notes} notes in batches of {BATCH_SIZE}...", file=sys.stderr)

    all_added_note_ids = []
    all_updated_note_info = []

    for i in range(0, total_notes, BATCH_SIZE):
        batch = notes[i:i + BATCH_SIZE]
        start_num = i + 1
        end_num = min(i + BATCH_SIZE, total_notes)
        print(f"\n--- Processing batch {start_num}-{end_num} ---", file=sys.stderr)

        notes_to_add, notes_to_update = get_ac_add_and_update_note_lists(batch)
        
        if notes_to_add:
            print(f'[+] Adding {len(notes_to_add)} new notes...', file=sys.stderr)
            added_ids = invoke_ac('addNotes', notes=notes_to_add)
            all_added_note_ids.extend(added_ids)
        else:
            print('[+] No new notes to add in this batch.', file=sys.stderr)

        if notes_to_update:
            print(f'[+] Updating {len(notes_to_update)} existing notes...', file=sys.stderr)
            find_note_actions = []
            for n in notes_to_update:
                unique_field_name = 'Quotation' if 'Quotation' in n['fields'] else 'Front'
                unique_field_value = n['fields'].get(unique_field_name, '').replace('"', '\\"')
                query = 'deck:"{}" "{}:{}"'.format(n['deckName'], unique_field_name, unique_field_value)
                find_note_actions.append(make_ac_request('findNotes', query=query))
            
            find_note_results = invoke_multi_ac(find_note_actions)

            new_notes_to_update, updated_note_info_results = ac_update_notes_and_get_note_info(
                notes_to_update, find_note_results)
            
            if new_notes_to_update:
                print(f'[+] Removing outdated tags from {len(new_notes_to_update)} notes...', file=sys.stderr)
                ac_remove_tags(new_notes_to_update, updated_note_info_results)
                all_updated_note_info.extend(updated_note_info_results)
        else:
            print('[+] No existing notes to update in this batch.', file=sys.stderr)

    print("\n--- Batch processing finished ---", file=sys.stderr)

    if suspend_cards:
        card_ids_to_suspend = []
        
        valid_added_ids = [nid for nid in all_added_note_ids if nid is not None]
        if valid_added_ids:
            print('[+] Fetching card info for new notes to suspend...', file=sys.stderr)
            added_note_info = invoke_ac('notesInfo', notes=valid_added_ids)
            for note_info in added_note_info:
                card_ids_to_suspend.extend(note_info['cards'])
        
        if all_updated_note_info:
            print('[+] Collecting card info for updated notes to suspend...', file=sys.stderr)
            for note_info_list in all_updated_note_info:
                for note_info in note_info_list:
                    card_ids_to_suspend.extend(note_info['cards'])
        
        if card_ids_to_suspend:
            print(f'[+] Suspending {len(card_ids_to_suspend)} cards in total...', file=sys.stderr)
            invoke_ac('suspend', cards=card_ids_to_suspend)

    return {
        "total_notes": total_notes,
        "added_count": len([nid for nid in all_added_note_ids if nid is not None]),
        "updated_count": len(all_updated_note_info),
        "decks": all_deck_names
    }

def download_csv(sheet_url):
    print('[+] Downloading CSV', file=sys.stderr)
    r = requests.get(sheet_url)
    path = None
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(r.content)
        path = f.name
    print('[+] Wrote CSV to {}'.format(f.name), file=sys.stderr)
    return f.name

def import_csv(col, csv_path, deck_name, note_type, allow_html, skip_header):
    import anki
    from anki.importing import TextImporter
    print('[+] Importing CSV from {}'.format(csv_path), file=sys.stderr)
    if skip_header:
        with tempfile.NamedTemporaryFile(delete=False, mode='w') as tmp:
            with open(csv_path, 'r') as f:
                tmp.writelines(f.read().splitlines()[1:])
                csv_path = tmp.name
        print('[+] Removed CSV header and wrote new file to {}'.format(csv_path), file=sys.stderr)
    did = col.decks.id(deck_name)
    col.decks.select(did)
    model = col.models.byName(note_type)
    deck = col.decks.get(did)
    deck['mid'] = model['id']
    col.decks.save(deck)
    model['did'] = did
    ti = anki.importing.TextImporter(col, csv_path)
    ti.allowHTML = allow_html
    ti.initMapping()
    ti.run()
    col.close()
    if skip_header:
        os.remove(csv_path)
    print('[+] Finished importing CSV', file=sys.stderr)

def parse_arguments():
    parser = argparse.ArgumentParser(
        description='Import a local or remote CSV/TSV file into Anki')

    parser.add_argument(
        '-p',
        '--path',
        help='the path of the local CSV/TSV file')
    parser.add_argument(
        '-u',
        '--url',
        help='the URL of the remote CSV file')

    parser.add_argument(
        '-d',
        '--deck',
        help='the name of the deck to import the sheet to (optional if Deck column exists in file)',
        required=False)
    parser.add_argument(
        '-n',
        '--note',
        help='the note type to import',
        required=True)
    
    parser.add_argument(
        '--deck-metadata-file',
        help='Path to a JSON file with deck metadata (e.g., descriptions).'
    )

    parser.add_argument(
        '-s', '--sync',
        help='Automatically trigger Anki synchronization after importing notes.',
        action='store_true')
    
    parser.add_argument(
        '--suspend',
        help='Suspend all newly added and updated cards upon import.',
        action='store_true')

    parser.add_argument(
        '--zid',
        help='Session ZID correlation identifier')
    parser.add_argument(
        '--trace-id',
        help='Trace correlation identifier')
    parser.add_argument(
        '--log-file',
        help='Path to structured import log file')

    parser.add_argument(
        '--no-anki-connect',
        help='write notes directly to Anki DB without using AnkiConnect',
        action='store_true')
    parser.add_argument(
        '-c',
        '--col',
        help='the path to the .anki2 collection (only when using --no-anki-connect)')
    parser.add_argument(
        '--allow-html',
        help='render HTML instead of treating it as plaintext (only when using --no-anki-connect)',
        action='store_true')
    parser.add_argument(
        '--skip-header',
        help='skip first row of CSV (only when using --no-anki-connect)',
        action='store_true')

    return parser.parse_args()


def validate_args(args):
    if args.path and args.url:
        print('[E] Only one of --path and --url can be supplied', file=sys.stderr)
        exit(1)

    if not (args.path or args.url):
        print('[E] You must specify either --path or --url', file=sys.stderr)
        exit(1)

    if args.no_anki_connect:
        if not args.col:
            print('[E] --col is required when using --no-anki-connect', file=sys.stderr)
            exit(1)
    else:
        if args.skip_header:
            print('[E] --skip-header is only supported with --no-anki-connect', file=sys.stderr)
            exit(1)
        elif args.allow_html:
            print('[E] --allow-html is only supported with --no-anki-connect, '
                  'when using AnkiConnect HTML is always enabled', file=sys.stderr)
            exit(1)
        elif args.col:
            print('[E] --col is only supported with --no-anki-connect', file=sys.stderr)
            exit(1)


def main():
    args = parse_arguments()
    validate_args(args)

    if args.url:
        csv_path = download_csv(args.url)
    elif args.path:
        csv_path = os.path.abspath(args.path)
    else:
        assert False

    zid = args.zid or ""
    if not zid and args.path:
        m = re.search(r'(\d{14})', os.path.basename(args.path))
        if m:
            zid = m.group(1)

    trace_id = args.trace_id or (f"{zid}:export:anki" if zid else "export:anki")

    log_file = args.log_file
    if not log_file and args.path and zid:
        p = Path(args.path)
        potential_log = p.parent / f"{zid}-import.log"
        if p.parent.name == 'favorites' or potential_log.exists():
            log_file = str(potential_log)

    if args.no_anki_connect:
        import anki
        col = anki.Collection(args.col)
        import_csv(
            col,
            csv_path,
            args.deck,
            args.note,
            args.allow_html,
            args.skip_header)
        print('[W] Cards cannot be automatically synced, '
              'open Anki to sync them manually', file=sys.stderr)
        summary = {"total_notes": 0, "decks": [args.deck] if args.deck else []}
    else:
        online, probe_err = probe_ankiconnect(ANKI_CONNECT_URL, timeout=2.0)
        if not online:
            emit_error_and_exit(
                code="ERR_ANKI_NOT_RUNNING",
                message=f"Cannot connect to AnkiConnect at {ANKI_CONNECT_URL.replace('http://', '')}. Make sure Anki is open.",
                details={"url": ANKI_CONNECT_URL, "error": probe_err},
                zid=zid,
                trace_id=trace_id,
                log_file=log_file
            )

        try:
            summary = send_to_anki_connect(
                csv_path,
                args.deck,
                args.note,
                args.suspend,
                args.deck_metadata_file)
        except ValueError as e:
            emit_error_and_exit(
                code="ERR_NOTE_TYPE_MISMATCH",
                message=str(e),
                details={"path": csv_path, "note_type": args.note},
                zid=zid,
                trace_id=trace_id,
                log_file=log_file
            )
        except Exception as e:
            emit_error_and_exit(
                code="ERR_ANKICONNECT_DISABLED" if "plugin" in str(e).lower() else "ERR_ANKI_NOT_RUNNING",
                message=f"Anki import error: {e}",
                details={"path": csv_path, "error": str(e)},
                zid=zid,
                trace_id=trace_id,
                log_file=log_file
            )

        if args.sync:
            print('[+] Syncing', file=sys.stderr)
            try:
                invoke_ac("sync")
            except Exception as e:
                print(f"[W] Sync warning: {e}", file=sys.stderr)
        else:
            print('[+] Import complete. Sync was skipped (use --sync to enable).', file=sys.stderr)

    deck_name_res = args.deck or (summary["decks"][0] if summary.get("decks") else "")
    total_cards_imported = summary.get("total_notes", 0)
    success_payload = {
        "status": "success",
        "zid": zid,
        "trace_id": trace_id,
        "cards_imported": total_cards_imported,
        "deck_name": deck_name_res,
        "message": f"Successfully imported {total_cards_imported} card(s) into Anki deck '{deck_name_res}'."
    }
    if log_file:
        write_import_log_entry(log_file, trace_id, "SUCCESS", success_payload["message"])
    print(json.dumps(success_payload))

    if args.url:
        os.remove(csv_path)
        print('[+] Removed temporary files', file=sys.stderr)


if __name__ == '__main__':
    main()