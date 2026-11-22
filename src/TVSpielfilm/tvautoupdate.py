########################################################################################################
# TV Spielfilm by Mr.Servo @OpenATV (c) 2026 - skinned by stein17 @OpenATV                             #
# Special thanks to jbleyel @OpenATV for his valuable support in creating the code.                    #
# -----------------------------------------------------------------------------------------------------#
# This plugin is licensed under the GNU version 3.0 <https://www.gnu.org/licenses/gpl-3.0.en.html>.    #
# This plugin is NOT free software. It is open source, you are allowed to modify it (if you keep       #
# the license), but it may not be commercially distributed. Advertise with this plugin is not allowed. #
# For other uses, permission from the authors is necessary.                                            #
########################################################################################################
# PYTHON IMPORTS
from datetime import datetime, timedelta
from os.path import isfile
from threading import Lock
from typing import Any, cast

# ENIGMA IMPORTS
from Components.config import config

# PLUGIN IMPORTS
from .tvcorehelper import TVCoreHelper
from .tvglobals import tvglobals


class TVAutoUpdate(TVCoreHelper):
	update_lock = Lock()

	def cache_asset_image(self, img_url):
		err_msg, img_file = self.cache_image(img_url)
		return err_msg if img_file or err_msg else "No image data returned"

	def cache_channel_details(self, channel_assets, channel_id):
		for asset_info in channel_assets:
			if tvglobals.AUTOUPDATESTOP:
				break
			asset_url = asset_info.get("asset_url", "")
			if not asset_url:
				continue
			err_msg, asset = self.get_single_asset(asset_url)
			if err_msg:
				print(f"[{self.MODULE_NAME}] ERROR in class 'TVAutoUpdate:cache_channel_details' - asset '{asset_url}' for channel '{channel_id}': {err_msg}")
				continue
			if tvglobals.AUTOUPDATESTOP:
				break
			if img_url := asset.get("img_url", ""):
				if err_msg := self.cache_asset_image(img_url):
					print(f"[{self.MODULE_NAME}] ERROR in class 'TVAutoUpdate:cache_channel_details' - image '{img_url}': {err_msg}")

	def update_future_epg(self, gui_output=None, force_refresh=True, today_only=False, timer_entry=None):
		cache_details = timer_entry is not None and gui_output is None
		with self.update_lock:
			if tvglobals.AUTOUPDATEACTIVE:
				print(f"[{self.MODULE_NAME}] INFO: TVS-EPG update is already active.")
				return False
			cast(Any, tvglobals).AUTOUPDATEACTIVE, tvglobals.AUTOUPDATESTOP = True, False
		succeeded = False
		err_msg = ""
		try:
			if err_msg := self.create_cache_paths():
				print(f"[{self.MODULE_NAME}] ERROR in class 'TVAutoUpdate:update_future_epg': {err_msg}")
				return False
			self.cleanup_cache()
			print(f"[{self.MODULE_NAME}] EPG update starts {timer_entry or ''}")
			import_dict = self.IMPORTDICT = self.read_imported_file()
			len_import_dict = len(import_dict)
			max_cache_days = 1 if today_only else config.plugins.tvspielfilm.cacherange.value
			if gui_output:
				gui_output.len_import_dict = len_import_dict
				gui_output.set_progress_range(0, (1, max_cache_days))
				gui_output.set_progress_range(1, (0, len_import_dict))
			progress = 0
			server_day = self.get_tvs_server_day(datetime.now(tz=None))
			for day in range(max_cache_days):
				if tvglobals.AUTOUPDATESTOP:
					break
				curr_date_dt = server_day + timedelta(days=day)
				weekday = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][curr_date_dt.weekday()] if day else "heute"
				if gui_output:
					gui_output.set_progress_values(0, (f"für '{weekday}', den {curr_date_dt.strftime('%d.%m.%Y')}", progress, f"{progress + 1}/{max_cache_days}"))
				progress += 1
				for index, (channel_id, channel_info) in enumerate(import_dict.items()):
					if tvglobals.AUTOUPDATESTOP:
						break
					if gui_output and not force_refresh and isfile(self.channel_assets_filepath(curr_date_dt, channel_id.lower())):
						continue
					if gui_output:
						gui_output.set_progress_values(1, (f"Sender: '{channel_info[1]}'", index + 1, f"{index + 1}/{len_import_dict}"))
					err_msg, channel_assets = self.get_channel_assets(curr_date_dt, channel_id.lower(), force_refresh=force_refresh)
					if err_msg:
						print(f"[{self.MODULE_NAME}] ERROR in class 'TVAutoUpdate:update_future_epg' - parsing of channel assets failed: {err_msg}")
						cast(Any, tvglobals).AUTOUPDATESTOP = True
						break
					if cache_details:
						self.cache_channel_details(channel_assets, channel_id)
				if tvglobals.AUTOUPDATESTOP:
					break
			succeeded = not tvglobals.AUTOUPDATESTOP
		except Exception as err:
			from traceback import print_exc
			print(f"[{self.MODULE_NAME}] Unexpected EPG update error: {err}")
			print_exc()
		finally:
			if tvglobals.AUTOUPDATESTOP:
				msg = f"EPG update stopped: {err_msg}" if err_msg else "EPG update stopped by scheduler."
			else:
				msg = "EPG update completed." if succeeded else "EPG update did not complete."
			print(f"[{self.MODULE_NAME}] {msg}")
			with self.update_lock:
				tvglobals.AUTOUPDATEACTIVE, tvglobals.AUTOUPDATESTOP = False, False
		return succeeded

	def autoupdate_epg(self, timer_entry=None):
		return self.update_future_epg(timer_entry=timer_entry)


tvautoupdate = TVAutoUpdate()
