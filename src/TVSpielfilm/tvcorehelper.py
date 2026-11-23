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
from glob import glob
from hashlib import md5
from io import BytesIO
from json import load, dump
from os import makedirs, remove, listdir
from os.path import isfile, isdir, join, getmtime, split, splitext
from re import findall
from shutil import copy, rmtree
from contextlib import suppress
from threading import Event, Lock
from typing import Any, Callable, Optional
from PIL import Image

# ENIGMA IMPORTS
from enigma import eEPGCache
from Components.config import config
from Components.UsageConfig import preferredTimerPath
from Screens.Timers import RecordTimerEdit, RecordTimerEntry
from ServiceReference import ServiceReference
from Tools.Directories import resolveFilename, SCOPE_CONFIG

# PLUGIN IMPORTS
from . import __version__
from .tvglobals import STARTTIMES, tvglobals
from .tvsparser import tvspassets, tvsphelper

_SINGLE_FLIGHT_LOCK = Lock()
_SINGLE_FLIGHTS = {}


class _SingleFlight:
	def __init__(self):
		self.event = Event()
		self.result: Any = None
		self.error: Optional[BaseException] = None


class TVCoreHelper(tvglobals):
	session: Any

	def _singleflight(self, key, action: Callable[[], Any]) -> Any:
		with _SINGLE_FLIGHT_LOCK:
			flight = _SINGLE_FLIGHTS.get(key)
			if flight is None:
				flight = _SingleFlight()
				_SINGLE_FLIGHTS[key] = flight
				is_owner = True
			else:
				is_owner = False
		if not is_owner:
			flight.event.wait()
			if flight.error is not None:
				raise flight.error
			return flight.result
		try:
			result = action()
		except BaseException as error:
			with _SINGLE_FLIGHT_LOCK:
				flight.error = error
				flight.event.set()
				_SINGLE_FLIGHTS.pop(key, None)
			raise
		with _SINGLE_FLIGHT_LOCK:
			flight.result = result
			flight.event.set()
			_SINGLE_FLIGHTS.pop(key, None)
		return result

	def is_already_listed(self, timespan_ts, sref, timer_list=None):
		if timespan_ts and len(timespan_ts) > 1 and sref:
			timer = f"{datetime.fromtimestamp(timespan_ts[0]).strftime('%F')}:::{datetime.fromtimestamp(timespan_ts[0]).strftime('%H:%M')}:::{sref}"
			return timer in (self.get_timer_list() if timer_list is None else timer_list)
		return False

	def get_timer_list(self):
		timerlist = []
		e2timer = resolveFilename(SCOPE_CONFIG, "timers.xml")
		if isfile(e2timer):
			timerxml = ""
			try:
				timerxml = open(e2timer).read()
			except OSError as err_msg:
				print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:get_timer_list': {err_msg}!")
			timers = findall(r'<timer begin="(.*?)" end=".*?" serviceref="(.*?)"', timerxml)
			for timer in timers:
				start = int(timer[0]) + int(config.recording.margin_before.value) * 60
				day = datetime.fromtimestamp(start).strftime("%F")
				hour = datetime.fromtimestamp(start).strftime("%H:%M")
				timerlist.append(f"{day}:::{hour}:::{timer[1]}")
		return timerlist

	def build_record_timer(self, start_ts, end_ts, sref, title, description, callback):
		if self.is_already_listed((start_ts, end_ts), sref):
			return False
		start_ts -= int(config.recording.margin_before.value) * 60
		end_ts += int(config.recording.margin_after.value) * 60
		service_ref = ServiceReference(sref)
		data = (start_ts, end_ts, title, description, None)
		new_entry = RecordTimerEntry(service_ref, checkOldTimers=False, dirname=preferredTimerPath(), fixDescription=True, *data)
		self.session.openWithCallback(callback, RecordTimerEdit, new_entry)
		return True

	def get_cache_path(self):
		config_path = config.plugins.tvspielfilm.cache_path.value
		return f"{config_path}tmp/TVSpielfilm" if config_path == "/" else f"{config_path}TVSpielfilm"

	def remove_cache_paths(self):
		cache_path = self.get_cache_path()
		paths = [f"{cache_path}/cache", f"{cache_path}/assets", f"{cache_path}/images", f"{cache_path}/spansets", cache_path]
		try:
			for path in paths:
				if isdir(path):
					rmtree(path)
		except OSError as err:
			err_msg = str(err)
			print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:create_cache_paths': {err_msg}!")
			return err_msg
		return ""

	def create_cache_paths(self):
		cache_path = self.get_cache_path()
		try:
			for path in [cache_path, f"{cache_path}/cache", f"{cache_path}/assets", f"{cache_path}/images"]:
				if not isdir(path):
					makedirs(path, exist_ok=True)
		except OSError as err:
			err_msg = str(err)
			print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:create_cache_paths': {err_msg}!")
			return err_msg
		return ""

	def cleanup_cache(self):  # delete older asset overviews, detailed assets and images
		def kill_file(file):
			with suppress(OSError):
				remove(file)

		now_dt = datetime.now(tz=None)
		latest = now_dt - timedelta(days=config.plugins.tvspielfilm.keepcache.value)
		latest_dateonly_dt = latest.replace(hour=0, minute=0, second=0, microsecond=0)
		cache_base = self.get_cache_path()
		for filepath in glob(join(f"{cache_base}/cache", "all_tips*.json")):  # treatment for tipps
			if int(now_dt.timestamp()) - int(getmtime(filepath)) > 86400:  # older than 24h?
				kill_file(filepath)
		for channel_id in self.IMPORTDICT.keys():  # treament for channel assets, channel by channel
			for filepath in glob(join(f"{cache_base}/cache/{channel_id}", "*.json")):
				if datetime.strptime(filepath.split("/")[-1][:10], "%Y-%m-%d") < latest_dateonly_dt:  # keepcache or older?
					kill_file(filepath)
		for root in [f"{cache_base}/assets", f"{cache_base}/images"]:  # treatment for single assets and images
			for entry in listdir(root):
				subpath = join(root, entry)
				if isdir(subpath):
					for filename in listdir(subpath):
						filepath = join(subpath, filename)
						if int(now_dt.timestamp()) - int(getmtime(filepath)) > 86400:  # older than 24h?
							kill_file(filepath)

	def check_outdated_structure(self):
		try:
			outdated = True if glob(join(self.get_cache_path(), "cache/allAssets*.json")) else False  # deprecated structure: e.g. 'allAssets_2026-10-24T20:15.json'
		except OSError as err:
			err_msg = str(err)
			print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:create_cache_paths': {err_msg}!")
			return err_msg, False
		return "", outdated

	def get_user_timespans(self):  # active timespans, e.g. timespans = [(('20:15', 'prime'), 105), (('22:00', '22'), 120), (('00:00', '0'), 120)]
		userconfigs = [
			(config.plugins.tvspielfilm.starttime_a.value, config.plugins.tvspielfilm.durance_a.value),
			(config.plugins.tvspielfilm.starttime_b.value, config.plugins.tvspielfilm.durance_b.value),
			(config.plugins.tvspielfilm.starttime_c.value, config.plugins.tvspielfilm.durance_c.value),
			(config.plugins.tvspielfilm.starttime_d.value, config.plugins.tvspielfilm.durance_d.value),
			(config.plugins.tvspielfilm.starttime_e.value, config.plugins.tvspielfilm.durance_e.value)
			]
		use_userconfigs = [
			config.plugins.tvspielfilm.use_a.value,
			config.plugins.tvspielfilm.use_b.value,
			config.plugins.tvspielfilm.use_c.value,
			config.plugins.tvspielfilm.use_d.value,
			config.plugins.tvspielfilm.use_e.value
			]
		return [(STARTTIMES[userconfig[0]], userconfig[1]) for index, userconfig in enumerate(userconfigs) if use_userconfigs[index]]

	def possible_search_dbs(self, asset, longtext=False):
		databases = 0
		if asset:
			self.curr_imdb_id = asset.get("imdb_id", "")
			self.curr_tmdb_id = asset.get("tmdb_id", "")
			database_bits = {"imdb_id": 1, "tmdb_id": 2}  # expand like this: "tvdb_id": 4
			for key, bit in database_bits.items():
				if asset.get(key):
					databases |= bit
		return databases

	def get_search_db_descs(self, databases, longtext=False):
		database_text = {
			0: ("Titelsuche", "Titelsuche"),
			1: ("IMDB", "IMDB (Internet Movie Database)"),
			2: ("TMDB", "TMDB (The Movie DataBase)"),
			3: ("IMDB+TMDB", "IMDB (Internet Movie Database) + TMDB (The Movie DataBase)"),
		}
		return database_text.get(databases, ("", ""))[1 if longtext else 0]

	def get_startsends_dt(self, curr_date_dt, span_starts_str, spandurance_ts, single_channel_id, time_code):
		hour, minute = span_starts_str.split(":") if span_starts_str else (curr_date_dt.strftime("%H"), curr_date_dt.strftime("%M"))
		if single_channel_id or time_code == "day":  # 'singleChannel' means 'the entire day' from 05:00h to 05:00h next morning, except it's before 05:00h
			span_starts_dt = curr_date_dt.replace(hour=5, minute=0, second=0, microsecond=0)  # start scan today 05:00h
			if curr_date_dt.hour < 5:  # if now is between 00:00h and 00500h, scan yesterday
				span_starts_dt -= timedelta(days=1)  # start scan yesterday 05:00h
			span_ends_dt = span_starts_dt + timedelta(days=1)
		elif time_code == "now":  # to also show long-running programs, the evaluation starts 5 hours before
			span_starts_dt = curr_date_dt - timedelta(minutes=300)
			span_ends_dt = curr_date_dt + timedelta(minutes=spandurance_ts)
		else:  # user time spans
			span_starts_dt = curr_date_dt.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
			span_ends_dt = span_starts_dt + timedelta(minutes=spandurance_ts)
			mid_night = (curr_date_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
			# correct timespan when timespan is over or on next day (00:00h to 05:00h)
			if span_ends_dt < curr_date_dt and span_starts_dt < mid_night:  # has the timespan already expired and is it before midnight?
				span_starts_dt += timedelta(days=1)
				span_ends_dt += timedelta(days=1)
		return span_starts_dt, span_ends_dt

	def create_cache_subpath(self, filepath):
		subpath, filename = split(filepath)
		if not isdir(subpath):
			makedirs(subpath, exist_ok=True)
		return

	def assets_filepath(self, span_starts_dt, time_code):
		if time_code == "now":
			filename = "assets_now.json"
		else:
			filename = f"assets_{span_starts_dt.strftime('%F')}T{span_starts_dt.strftime('%H:%M')}.json"
		return join(f"{self.get_cache_path()}/cache", filename)

	def image_filename(self, img_url):
		name, ext = splitext(img_url[img_url.find(".de") + 3:img_url.rfind("?im")])
		filename = f"{md5(name.encode()).hexdigest()}{ext}"  # NOSONAR
		cache_path = f"{self.get_cache_path()}/images/{filename[0]}"  # e.g. '/media/hdd/TVSpielfilm/images/a'
		return join(cache_path, filename.replace('.jpeg', '.jpg')) if img_url else ""

	def tips_filename(self):
		return join(f"{self.get_cache_path()}/cache", f"all_tips_{datetime.now(tz=None).strftime('%F')}.json")

	def single_asset_filename(self, asset_url):
		filename = ""
		if asset_url:
			hash = md5(asset_url.encode()).hexdigest()  # NOSONAR
			cache_path = f"{self.get_cache_path()}/assets"  # e.g. '/media/hdd/TVSpielfilm/assets'
			filename = join(f"{cache_path}/{hash[0]}", f"{hash}.json")  # e.g. '/media/.../5/533..A.json'
		return filename

	def get_single_asset(self, asset_url, callback=None):
		if asset_url:
			err_msg, asset = self._singleflight(("asset", asset_url), lambda: self._get_single_asset_uncached(asset_url))
		else:
			err_msg, asset = "Missing 'asset_url'", {}
			print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:get_single_asset': {err_msg}!")
		if callback:
			callback(err_msg, asset)
		return err_msg, asset

	def _get_single_asset_uncached(self, asset_url):
		err_msg, asset, cached_asset = "", {}, {}
		filepath = self.single_asset_filename(asset_url)
		if filepath and isfile(filepath):
			err_msg, tvs_version, cached_asset = self.load_jsonlist(filepath)
			if not err_msg and cached_asset.get("image_parser_version") == tvspassets.IMAGE_PARSER_VERSION:
				return err_msg, cached_asset
		err_msg, asset = tvspassets.parse_single_asset(asset_url)
		if not err_msg and asset:
			err_msg = self.save_jsonlist(asset, filepath)
			if err_msg:
				print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:get_single_asset': {err_msg}!")
		elif cached_asset:
			cached_asset["img_url"] = cached_asset.get("img_url", "").partition('"')[0]
			return "", cached_asset
		return err_msg, asset

	def cache_image(self, img_url) -> tuple[str, str]:
		img_file = self.image_filename(img_url) if img_url else ""
		if not img_file:
			return "", ""

		def download_image():
			if isfile(img_file):
				return "", img_file
			err_msg, binary = tvsphelper.get_binary_data(img_url)
			if err_msg or not binary:
				return err_msg or "No image data returned", ""
			try:
				with Image.open(BytesIO(binary)) as img:
					img.thumbnail((600, 450) if self.RESOLUTION == "FHD" else (400, 300), Image.Resampling.LANCZOS)
					self.create_cache_subpath(img_file)
					img.save(img_file, format="jpeg", quality=25, optimize=True)
			except (OSError, ValueError) as err:
				return str(err), ""
			return "", img_file

		return self._singleflight(("image", img_file), download_image)

	def channel_assets_filepath(self, curr_date_dt, channel_id):
		filename = ""
		if curr_date_dt and channel_id:
			server_day = self.get_tvs_server_day(curr_date_dt)
			cache_path = f"{self.get_cache_path()}/cache/{channel_id}"  # e.g. '/media/hdd/TVSpielfilm/cache/ard'
			filename = join(f"{cache_path}", f"{server_day.strftime('%F')}.json")
		return filename

	def get_tvs_server_day(self, curr_date_dt):
		server_day = curr_date_dt.replace(hour=5, minute=0, second=0, microsecond=0)
		if curr_date_dt.hour < 5:
			server_day -= timedelta(days=1)
		return server_day

	def get_channel_assets(self, curr_date_dt, channel_id, force_refresh=False):
		err_msg, channel_assets = "", []
		if curr_date_dt and channel_id:
			server_day = self.get_tvs_server_day(curr_date_dt)
			filepath = self.channel_assets_filepath(curr_date_dt, channel_id)
			if not force_refresh and filepath and isfile(filepath):
				err_msg, tvs_version, channel_assets = self.load_jsonlist(filepath)
				return err_msg, channel_assets
			err_msg, channel_assets = tvspassets.parse_channel_page(channel_id, server_day, "day")
			if not err_msg and channel_assets:
				err_msg = self.save_jsonlist(channel_assets, filepath)
				if err_msg:
					print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:get_channel_assets': {err_msg}!")
		else:
			err_msg = "Missing 'curr_date_dt' or 'userspan'"
			print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:get_channel_assets': {err_msg}!")
		return err_msg, channel_assets

	def load_jsonlist(self, filepath):
		err_msg, jsonlist = "", {}
		try:
			with open(filepath) as file:
				jsonlist = load(file)
		except OSError as err:
			err_msg = str(err)
			print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:load_jsonlist': {err_msg}!")
		return err_msg, jsonlist.get("tvs_version", ""), jsonlist.get("data", [])

	def save_jsonlist(self, jsonlist, filepath):
		err_msg = ""
		if jsonlist and filepath:
			jsondict = {}
			jsondict["tvs_version"] = "".join(x for x in __version__ if x.isdigit() or x == ".")  # "v3.0alpha" -> '3.0'
			jsondict["data"] = jsonlist
			self.create_cache_subpath(filepath)
			try:
				with open(filepath, "w") as file:
					dump(jsondict, file)
			except OSError as err:
				err_msg = str(err)
				print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:save_jsonlist': {err_msg}!")
		return err_msg

	def get_current_asset_url(self, ref, callback=None):
		epg = eEPGCache.getInstance()
		ptr = ref and ref.valid() and epg.lookupEventTime(ref, -1)
		asset_url = ""
		if ptr:
			event_start_ts = ptr.getBeginTime()
			if epg.lookupEventTime(ref, event_start_ts, +1):  # Event
				if channel_id := [shortcut for shortcut, detail in self.read_supported_file().items() if detail[0] == ref.toString()]:
					channel_id = channel_id[0].lower()
					event_start_ft = datetime.fromtimestamp(event_start_ts)
					event_start_ft -= timedelta(minutes=event_start_ft.minute % 15, seconds=event_start_ft.second, microseconds=0)  # round off to last 15 minutes
					err_msg, channel_assets = tvspassets.parse_channel_page(channel_id, event_start_ft, "now")  # get 'Jetzt im TV' for current channel_id
					if err_msg:
						print(f"[{self.MODULE_NAME}] ERROR in class 'TVCorehelper:get_current_asset_url' - parsing failed: {err_msg}")
						return ""
					for asset in channel_assets:
						timestart_iso = asset.get("time_start", "")
						try:
							timestart_dt = datetime.fromisoformat(timestart_iso).replace(tzinfo=None) if timestart_iso else datetime.now(tz=None)
						except (TypeError, ValueError):
							continue
						if timestart_dt and timestart_dt >= event_start_ft:
							asset_url = asset.get("asset_url", "")
							break
					if asset_url:
						self.get_single_asset(asset_url)  # simply download and save to cache if it does not already exist
		if callback:
			callback(asset_url)
		return asset_url

	def read_imported_file(self):
		import_dict = {}
		if isfile(self.IMPORTFILE):
			try:
				with open(self.IMPORTFILE) as file:
					import_dict = load(file)
			except OSError as err:
				err_msg = str(err)
				print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:read_imported_file': {err_msg}!")
		return import_dict  # e.g. [('ard': ('1:0:27:212F:31B:1:FFFF0000:0:0:0:', 'Das Erste HD')), ...]

	def read_supported_file(self):
		supp_dict = {}
		if isfile(self.SUPPFILE):
			try:
				with open(self.SUPPFILE) as file:
					supp_dict = load(file)
			except OSError as err_msg:
				print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:read_supported_file': {err_msg}!")
		return supp_dict  # e.g. {'ard': ['1:0:19:283D:41B:1:FFFF0000:0:0:0:', 'Das Erste HD'], 'zdf': [...], ...}

	def update_mappingfile(self):
		if not isdir(self.CONFIGPATH):
			makedirs(self.CONFIGPATH, exist_ok=True)
		sourcefile = join(self.PLUGINPATH, "db/tvs_mapping.txt")
		try:
			if isfile(self.MAPFILE) and (config.plugins.tvspielfilm.update_mapfile.value and int(getmtime(sourcefile)) > int(getmtime(self.MAPFILE))):  # plugin mapfile older than user mapfile:
				hashcode = ""
				with open(self.MAPFILE, "rb") as file:
					hashcode = md5(file.read()).hexdigest()  # NOSONAR
				if hashcode != config.plugins.tvspielfilm.mapfilehash.value:  # has the content of the mapfile changed?
					print(f"[{self.MODULE_NAME}] Copy '{sourcefile}' to '{self.MAPFILE}'.")
					copy(sourcefile, self.MAPFILE)
					config.plugins.tvspielfilm.mapfilehash.value = hashcode
					config.plugins.tvspielfilm.mapfilehash.save()
					return True
			elif not isfile(self.MAPFILE):
				print(f"[{self.MODULE_NAME}] Copy '{sourcefile}' to '{self.MAPFILE}'.")
				copy(sourcefile, self.MAPFILE)
				return True
		except OSError as err_msg:
			print(f"[{self.MODULE_NAME}] ERROR in class 'TVCoreHelper:update_mappingfile': {err_msg}!")
		return False


tvcorehelper = TVCoreHelper()
