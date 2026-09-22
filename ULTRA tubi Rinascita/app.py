import webview
import backend_logic as logic
import os
import re
import traceback
import threading

class Api:
    def __init__(self):
        print("--- API INITIALIZING ---")
        self.config = logic.load_config() or {}
        print(f"DEBUG: Loaded config file content: {self.config}")
        self.da_fare_path = self.config.get('da_fare_path', None)
        print(f"DEBUG: Extracted DA FARE path: {self.da_fare_path}")
        self.last_scan_data = []
        self.piece_id_to_path_map = {}
        self.history_id_to_path_map = {}
        self._inventory_request_stop = threading.Event()
        self._inventory_request_thread = threading.Thread(
            target=self._inventory_request_loop,
            name="inventory-request-watcher",
            daemon=True,
        )
        self._inventory_request_thread.start()

    def _inventory_request_loop(self):
        while not self._inventory_request_stop.is_set():
            try:
                config = logic.load_config(silent=True) or {}
                logic.process_inventory_export_request(
                    config,
                    config.get("da_fare_path") or self.da_fare_path,
                )
            except Exception:
                print("--- PYTHON ERROR in inventory request watcher ---")
                traceback.print_exc()
            self._inventory_request_stop.wait(2.0)

    def shutdown(self, *args):
        self._inventory_request_stop.set()

    def get_initial_data(self):
        self.config = logic.load_config() or {}
        self.da_fare_path = self.config.get('da_fare_path', None)
        return {'da_fare_path': self.da_fare_path}

    def get_config_settings(self):
        self.config = logic.load_config() or {}
        return {"status": "success", "config": self.config}

    def save_config_settings(self, payload):
        payload = payload or {}
        updated = dict(logic.load_config() or {})
        path_keys = (
            "da_fare_path",
            "inventory_xlsx_path",
            "database_tubi_dir",
            "codici_tubi_path",
            "docx_output_dir",
            "summary_txt_output_dir",
            "summary_docx_output_dir",
            "tt_docx_output_dir",
            "adhoc_docx_output_dir",
            "codes_docx_output_dir",
            "inventory_xlsx_output_dir",
            "order_txt_output_dir",
            "inventory_request_folder",
        )
        for key in path_keys:
            updated[key] = str(payload.get(key) or "").strip()

        updated["inventory_request_enabled"] = bool(payload.get("inventory_request_enabled"))
        updated["order_include_low_priority"] = bool(
            payload.get("order_include_low_priority", updated.get("order_include_low_priority", True))
        )
        updated.pop("inventory_request_include_missing_codes", None)
        for key, default in (
            ("order_priority_low_quantity", 1),
            ("order_priority_medium_quantity", 3),
            ("order_priority_high_quantity", 5),
        ):
            try:
                updated[key] = max(0, int(payload.get(key, default)))
            except (TypeError, ValueError):
                updated[key] = default
        for key, fallback in (
            ("lock_unlocked_color", "#dc3545"),
            ("lock_locked_color", "#198754"),
        ):
            value = str(payload.get(key) or fallback).strip()
            updated[key] = value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else fallback

        ignored = []
        ignored_seen = set()
        for value in payload.get("ignore_folders", []):
            name = str(value or "").strip()
            normalized = name.lower()
            if name and normalized not in ignored_seen:
                ignored.append(name)
                ignored_seen.add(normalized)
        updated["ignore_folders"] = ignored

        response = logic.save_config(updated)
        if response.get("status") == "success":
            self.config = updated
            self.da_fare_path = updated.get("da_fare_path") or None
            request_folder = updated.get("inventory_request_folder")
            if request_folder:
                logic.initialize_inventory_request_folder(request_folder)
            return {"status": "success", "config": updated}
        return response

    def pick_settings_folder(self):
        result = window.create_file_dialog(webview.FOLDER_DIALOG)
        if result:
            return {"status": "success", "path": result[0]}
        return {"status": "cancelled"}

    def initialize_inventory_request_folder(self, folder_path):
        try:
            return logic.initialize_inventory_request_folder(folder_path)
        except Exception:
            traceback.print_exc()
            return {"status": "error", "message": "Impossibile inizializzare la cartella condivisa."}

    def pick_inventory_excel_file(self):
        try:
            result = window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("File Excel (*.xlsx;*.xlsm)", "Tutti i file (*.*)"),
            )
        except TypeError:
            result = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
        if result:
            return {"status": "success", "path": result[0]}
        return {"status": "cancelled"}

    def pick_code_catalog_file(self):
        try:
            result = window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("File JSON (*.json)", "Tutti i file (*.*)"),
            )
        except TypeError:
            result = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
        if result:
            return {"status": "success", "path": result[0]}
        return {"status": "cancelled"}

    def get_tubes_state(self):
        try:
            self.config = logic.load_config() or {}
            self.da_fare_path = self.config.get('da_fare_path', None)
            self.last_scan_data = logic.get_current_state(self.da_fare_path)
            if self.last_scan_data is None:
                return None
            self.piece_id_to_path_map.clear()
            for group in self.last_scan_data:
                for piece in group.get('pieces', []):
                    piece_id, file_path = piece.get('id'), piece.get('filePath')
                    if piece_id and file_path:
                        self.piece_id_to_path_map[piece_id] = file_path
            return self.last_scan_data
        except Exception:
            print("--- PYTHON ERROR in get_tubes_state ---")
            traceback.print_exc()
            return None

    def nest_piece_instances(self, piece_instances, rod_length=6000):
        try:
            return logic.nest_piece_instances(piece_instances or [], rod_length)
        except Exception:
            print("--- PYTHON ERROR in nest_piece_instances ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante il nesting backend.", "rods": []}

    def get_unmatched_igs_files(self):
        try:
            return logic.find_unmatched_igs_files(self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in get_unmatched_igs_files ---")
            traceback.print_exc()
            return []

    def get_zzx_validation_issues(self):
        try:
            return logic.find_zzx_validation_issues(self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in get_zzx_validation_issues ---")
            traceback.print_exc()
            return []

    def open_file_path(self, file_path):
        return logic.open_file_in_explorer(file_path)

    def get_debug_info(self):
        return {
            "project_folder_path": logic.PROJECT_ROOT,
            "variables_root": logic.VARIABLES_ROOT,
            "using_external_variables": logic.USING_EXTERNAL_VARIABLES,
            "app_data_path": logic.APP_DATA_PATH,
            "config_file_path": logic.CONFIG_FILE,
            "state_file_path": logic.UI_STATE_FILE,
            "history_file_path": logic.HISTORY_FILE,
            "loaded_config_content": self.config,
            "da_fare_path_in_use": self.da_fare_path,
            "inventory_debug": logic.get_inventory_debug(self.da_fare_path),
        }

    def get_tube_database(self, filters=None):
        try:
            return logic.get_tube_database_rows(filters or {}, self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in get_tube_database ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante il caricamento Database Tubi.", "rows": []}

    def import_tube_database_from_excel(self):
        try:
            self.config = logic.load_config() or {}
            self.da_fare_path = self.config.get('da_fare_path', None)
            return logic.import_tube_database_from_excel(da_fare_path=self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in import_tube_database_from_excel ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante l'importazione del Database Tubi."}

    def validate_tube_database(self):
        try:
            return logic.validate_tube_database_against_excel(self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in validate_tube_database ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante la validazione del Database Tubi."}

    def add_tube_database_item(self, payload):
        try:
            return logic.add_tube_database_item(payload or {}, self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in add_tube_database_item ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante l'aggiunta del tubo."}

    def remove_tube_database_item(self, payload):
        try:
            return logic.remove_tube_database_item(payload or {}, self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in remove_tube_database_item ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante la rimozione del tubo."}

    def set_tube_database_item_quantity(self, payload):
        try:
            return logic.set_tube_database_item_quantity(payload or {}, self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in set_tube_database_item_quantity ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante la modifica della quantita."}

    def move_tube_database_item(self, payload):
        try:
            return logic.move_tube_database_item(payload or {}, self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in move_tube_database_item ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante lo spostamento del tubo."}

    def import_tube_code_catalog(self):
        try:
            return logic.import_tube_code_catalog()
        except Exception:
            print("--- PYTHON ERROR in import_tube_code_catalog ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante l'importazione dei codici."}

    def set_tube_code_catalog_entry(self, payload):
        try:
            return logic.set_tube_code_catalog_entry(payload or {})
        except Exception:
            print("--- PYTHON ERROR in set_tube_code_catalog_entry ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante il salvataggio del codice."}

    def get_tube_order_priorities(self):
        try:
            return logic.get_tube_order_priorities(self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in get_tube_order_priorities ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante il caricamento delle priorita ordini."}

    def set_tube_order_priorities(self, payload):
        try:
            return logic.set_tube_order_priorities(payload or {}, self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in set_tube_order_priorities ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante il salvataggio delle priorita ordini."}

    def generate_tube_order_txt(self):
        try:
            return logic.generate_tube_order_txt(self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in generate_tube_order_txt ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante la generazione della lista ordine tubi."}

    def generate_adhoc_docx(self):
        try:
            return logic.generate_adhoc_docx(self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in generate_adhoc_docx ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante la generazione Lista ADHOC."}

    def generate_tube_codes_docx(self):
        try:
            return logic.generate_tube_codes_docx(self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in generate_tube_codes_docx ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante la generazione Lista Codici."}

    def generate_tube_inventory_xlsx(self, include_missing_codes=True):
        try:
            return logic.generate_tube_inventory_xlsx(bool(include_missing_codes), self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in generate_tube_inventory_xlsx ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante la generazione dell'inventario Excel."}

    def reset_adhoc_baseline(self):
        try:
            return logic.reset_adhoc_baseline(self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in reset_adhoc_baseline ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante l'azzeramento ADHOC."}

    def add_tube_database_scaffolding(self, payload):
        try:
            return logic.add_tube_database_scaffolding(payload or {}, self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in add_tube_database_scaffolding ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante l'aggiunta della scaffalatura."}

    def remove_tube_database_scaffolding(self, payload):
        try:
            return logic.remove_tube_database_scaffolding(payload or {}, self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in remove_tube_database_scaffolding ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante la rimozione della scaffalatura."}

    def add_tube_database_ripiano(self, payload):
        try:
            return logic.add_tube_database_ripiano(payload or {}, self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in add_tube_database_ripiano ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante l'aggiunta del ripiano."}

    def remove_tube_database_ripiano(self, payload):
        try:
            return logic.remove_tube_database_ripiano(payload or {}, self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in remove_tube_database_ripiano ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante la rimozione del ripiano."}

    def get_tube_database_positions_for_tube_type(self, tube_type):
        try:
            return logic.get_tube_database_positions_for_tube_type(tube_type, self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in get_tube_database_positions_for_tube_type ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante la lettura delle posizioni.", "positions": []}

    def decrement_tube_database_position(self, payload):
        try:
            return logic.decrement_tube_database_position(payload or {}, self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in decrement_tube_database_position ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante il decremento del database."}

    def select_and_save_da_fare_folder(self):
        result = window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            folder_path = result[0]
            self.config['da_fare_path'] = folder_path
            response = logic.save_config(self.config)
            if response.get('status') == 'success':
                self.da_fare_path = folder_path
                return {"status": "success", "path": folder_path}
            return response
        return {"status": "cancelled"}

    def open_folder_by_id(self, piece_id):
        file_path = self.piece_id_to_path_map.get(piece_id) or self.history_id_to_path_map.get(piece_id)
        if file_path:
            return logic.open_file_in_explorer(file_path)
        return {"status": "error", "message": f"Could not find file path for ID {piece_id}"}

    def process_quantity_update_by_id(self, piece_id, new_completed_count):
        file_path = self.piece_id_to_path_map.get(piece_id)
        if file_path:
            return logic.process_quantity_update(file_path, new_completed_count, self.da_fare_path)
        return {"status": "error", "message": f"Could not find file path for ID {piece_id}"}

    def mark_rod_fatto_by_ids(self, piece_ids):
        rod_pieces_data = []
        for pid in piece_ids:
            found = False
            for group in self.last_scan_data:
                for piece in group.get('pieces', []):
                    if piece.get('id') == pid:
                        rod_pieces_data.append(piece)
                        found = True
                        break
                if found:
                    break
        if not rod_pieces_data:
            return {"status": "error", "message": "Could not find piece data for given IDs."}
        return logic.process_rod_completion(rod_pieces_data, self.da_fare_path)

    def mark_locked_segments_fatto(self, segments):
        try:
            return logic.process_locked_segments_completion(segments or [], self.da_fare_path)
        except Exception:
            print("--- PYTHON ERROR in mark_locked_segments_fatto ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante l'aggiornamento dei pezzi bloccati."}

    def log_locked_rod_completion(self, rod_payload):
        try:
            return logic.log_locked_rod_completion(rod_payload or {})
        except Exception:
            print("--- PYTHON ERROR in log_locked_rod_completion ---")
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante il salvataggio in cronologia."}

    def load_ui_state(self):
        return logic.load_ui_state()

    def save_ui_state(self, state_data):
        return logic.save_ui_state(state_data)

    def get_history(self):
        history_data = logic.get_history_from_log()
        self.history_id_to_path_map.clear()
        for rod in history_data:
            for piece in rod.get('pieces', []):
                self.history_id_to_path_map[piece['id']] = piece['filePath']
        return history_data

    def remove_history_items(self, rod_ids):
        return logic.remove_history_items(rod_ids)

    def undo_rod_by_id(self, rod_id):
        return logic.undo_rod_completion(rod_id, self.da_fare_path)

    def get_summary_data(self, threshold):
        if not self.last_scan_data:
            return {"status": "success", "text": "Dati non ancora caricati. Clicca 'Aggiorna' prima."}
        return logic.generate_summary_text(self.last_scan_data, float(threshold), self.da_fare_path)

    def get_summary_data_for_plan(self, threshold, plan_data):
        if not plan_data:
            return self.get_summary_data(threshold)
        return logic.generate_summary_text(plan_data, float(threshold), self.da_fare_path)

    def save_and_open_summary(self, summary_text):
        return logic.save_summary_and_open(summary_text, self.da_fare_path)

    def save_and_open_summary_docx(self, summary_text):
        return logic.save_summary_and_open_docx(summary_text, self.da_fare_path)


    def generate_tt_docx_from_explorer_selection(self):
        """Generates TT list DOCX for currently selected folders in File Explorer."""
        try:
            return logic.generate_tt_docx_from_explorer_selection(self.da_fare_path)
        except Exception:
            traceback.print_exc()
            return {"status": "error", "message": "Errore durante la generazione DOCX TT."}

    def search_tube_inventory(self, dimensione, spessore, materiale, finitura):
        return logic.search_tube_inventory(dimensione, spessore, materiale, finitura, self.da_fare_path)

    def search_tube_inventory_by_type(self, tube_type):
        return logic.search_tube_inventory_by_type(tube_type, self.da_fare_path)

if __name__ == '__main__':
    api = Api()
    window = webview.create_window(
        'Tubi Nesting Manager',
        os.path.join(logic.RESOURCE_ROOT, 'web', 'index.html'),
        js_api=api,
        width=1200,
        height=800
    )
    window.events.closed += api.shutdown
    webview.start(debug=True)
