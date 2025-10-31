#!/usr/bin/env python3

import argparse
import csv
import requests
import os
import tempfile

ANKI_CONNECT_URL = 'http://localhost:8765'


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
        print('[E] Failed to connect to AnkiConnect, make sure Anki is running')
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
        header = next(reader)
        for j, field_name in enumerate(header):
            index_to_field_name[j] = field_name
        
        has_deck_column = 'Deck' in header
        
        if not deck_name and not has_deck_column:
            raise ValueError("[E] --deck is required when no 'Deck' column is present in the file")

        for row in reader:
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
                # This case should be rare now due to the check above, but as a safeguard:
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
                  'please report this to the developer'.format(unique_field_name, unique_field_value))
            continue
        elif len(find_note_result) > 1:
            print('[W] Duplicate notes are not supported, '
                  'skipping note with {} "{}"'.format(unique_field_name, unique_field_value))
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


def send_to_anki_connect(tsv_path, deck_name, note_type, suspend_cards):
    notes = tsv_to_ac_notes(tsv_path, deck_name, note_type)

    all_deck_names = sorted(list(set(note['deckName'] for note in notes)))
    print(f"[+] Found {len(all_deck_names)} unique decks. Ensuring they exist...")
    for deck in all_deck_names:
        invoke_ac('createDeck', deck=deck)

    notes_to_add, notes_to_update = get_ac_add_and_update_note_lists(notes)
    
    print('[+] Adding {} new notes...'.format(len(notes_to_add)))
    added_note_ids = invoke_ac('addNotes', notes=notes_to_add)

    print('[+] Updating {} existing notes...'.format(len(notes_to_update)))
    find_note_actions = []
    for n in notes_to_update:
        unique_field_name = 'Quotation' if 'Quotation' in n['fields'] else 'Front'
        unique_field_value = n['fields'].get(unique_field_name, '').replace('"', '\\"')
        query = 'deck:"{}" "{}:{}"'.format(n['deckName'], unique_field_name, unique_field_value)
        find_note_actions.append(make_ac_request('findNotes', query=query))
    find_note_results = invoke_multi_ac(find_note_actions)

    new_notes_to_update, updated_note_info_results = ac_update_notes_and_get_note_info(
        notes_to_update, find_note_results)

    print('[+] Removing outdated tags from notes')
    ac_remove_tags(new_notes_to_update, updated_note_info_results)

    if suspend_cards:
        card_ids_to_suspend = []
        
        if [nid for nid in added_note_ids if nid is not None]:
            print('[+] Fetching card info for new notes to suspend...')
            valid_added_ids = [nid for nid in added_note_ids if nid is not None]
            added_note_info = invoke_ac('notesInfo', notes=valid_added_ids)
            for note_info in added_note_info:
                card_ids_to_suspend.extend(note_info['cards'])
        
        if updated_note_info_results:
            print('[+] Collecting card info for updated notes to suspend...')
            for note_info_list in updated_note_info_results:
                for note_info in note_info_list:
                    card_ids_to_suspend.extend(note_info['cards'])
        
        if card_ids_to_suspend:
            print(f'[+] Suspending {len(card_ids_to_suspend)} cards...')
            invoke_ac('suspend', cards=card_ids_to_suspend)

def download_csv(sheet_url):
    print('[+] Downloading CSV')
    r = requests.get(sheet_url)
    path = None
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(r.content)
        path = f.name
    print('[+] Wrote CSV to {}'.format(f.name))
    return f.name

def import_csv(col, csv_path, deck_name, note_type, allow_html, skip_header):
    import anki
    from anki.importing import TextImporter
    print('[+] Importing CSV from {}'.format(csv_path))
    if skip_header:
        with tempfile.NamedTemporaryFile(delete=False, mode='w') as tmp:
            with open(csv_path, 'r') as f:
                tmp.writelines(f.read().splitlines()[1:])
                csv_path = tmp.name
        print('[+] Removed CSV header and wrote new file to {}'.format(csv_path))
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
    print('[+] Finished importing CSV')

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
        '-s', '--sync',
        help='Automatically trigger Anki synchronization after importing notes.',
        action='store_true')
    
    parser.add_argument(
        '--suspend',
        help='Suspend all newly added and updated cards upon import.',
        action='store_true')

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
        print('[E] Only one of --path and --url can be supplied')
        exit(1)

    if not (args.path or args.url):
        print('[E] You must specify either --path or --url')
        exit(1)

    if args.no_anki_connect:
        if not args.col:
            print('[E] --col is required when using --no-anki-connect')
            exit(1)
    else:
        if args.skip_header:
            print('[E] --skip-header is only supported with --no-anki-connect')
            exit(1)
        elif args.allow_html:
            print('[E] --allow-html is only supported with --no-anki-connect, '
                  'when using AnkiConnect HTML is always enabled')
            exit(1)
        elif args.col:
            print('[E] --col is only supported with --no-anki-connect')
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
              'open Anki to sync them manually')
    else:
        send_to_anki_connect(
            csv_path,
            args.deck,
            args.note,
            args.suspend)

        if args.sync:
            print('[+] Syncing')
            invoke_ac("sync")
        else:
            print('[+] Import complete. Sync was skipped (use --sync to enable).')

    if args.url:
        os.remove(csv_path)
        print('[+] Removed temporary files')


main()