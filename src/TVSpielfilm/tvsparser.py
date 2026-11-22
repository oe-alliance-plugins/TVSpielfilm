#######################################################################################################
#                                                                                                     #
#  tvsparser is a multiplatform tool (runs on Enigma2 & Windows and probably many others)             #
#  Coded by Mr.Servo @ openATV (c) 2025                                                               #
#  Learn more about the tool by running it in the shell: "python Buildstatus.py -h"                   #
#  ---------------------------------------------------------------------------------------------------#
#  This plugin is licensed under the GNU version 3.0 <https://www.gnu.org/licenses/gpl-3.0.en.html>.  #
#  This plugin is NOT free software. It is open source, you are allowed to modify it (if you keep     #
#  the license), but it may not be commercially distributed. Advertise with this tool is not allowed. #
#  For other uses, permission from the authors is necessary.                                          #
#                                                                                                     #
#######################################################################################################

# PYTHON IMPORTS
from datetime import datetime, timedelta
from getopt import getopt, GetoptError
from html import unescape
from json import dump, loads
from re import search, findall, S
from requests import get, exceptions
from secrets import choice
from sys import exit, argv
from typing import cast
from urllib.parse import urlencode

# TWISTED IMPORTS
from twisted.internet import reactor
from twisted.internet.threads import blockingCallFromThread, deferToThread
from twisted.web.client import Agent, ContentDecoderAgent, GzipDecoder, RedirectAgent, readBody
from twisted.web.http_headers import Headers
from twisted.web.iweb import IAgent


class TVSparserGlobals:
	MODULE_NAME = __name__.split(".")[-1]
	WEBURL = bytes.fromhex("68747470733a2f2f6d2e7476737069656c66696c6d2e64653"[:-1]).decode()
	WEBACCESS = None
	USERAGENT = choice([
			"Mozilla/5.0 (Windows NT 11.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36 Edg/129.0.2792.65",
			"Mozilla/5.0 (Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0 Viewer/98.9.5608.9",
			"Mozilla/5.0 (Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/104.0.0.0",
			"Mozilla/5.0 (Mozilla/5.0 (Macintosh; Intel Mac OS X 15.0; rv:130.0) Gecko/20100101 Firefox/130.0",
			"Mozilla/5.0 (Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0 beta 7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17 Safari/605.1.15)",
			"Mozilla/5.0 (Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.6668.89 Safari/537.36)"
			])
	LINES_PER_PAGE = 20  # fallback value


tvspglobals = TVSparserGlobals()


class TVSparserHelper:
	def request(self, url, params=None, headers=None, timeout=(3.05, 6)):
		# Blocking GET for use in worker threads, returns (status code, body bytes), raises on network errors
		if params:
			url = f"{url}?{urlencode({key: value for key, value in params.items() if value is not None}, doseq=True)}"
		headers = Headers({key: [value] for key, value in (headers or {"User-Agent": tvspglobals.USERAGENT}).items()})

		def fetch():
			agent = ContentDecoderAgent(RedirectAgent(cast(IAgent, Agent(reactor, connectTimeout=timeout[0]))), [(b"gzip", GzipDecoder)])
			deferred = agent.request(b"GET", url.encode(), headers)
			deferred.addCallback(lambda response: readBody(response).addCallback(lambda body: (response.code, body)))
			deferred.addTimeout(sum(timeout), reactor)
			return deferred

		return blockingCallFromThread(reactor, fetch)

	def check_webaccess(self):
		try:
			response = get(tvspglobals.WEBURL, headers={"User-Agent": tvspglobals.USERAGENT})
			try:
				if "challenge" in response.headers.get("cf-mitigated", "").lower():
					setattr(tvspglobals, "WEBACCESS", False)
					return "Cloudflare-Challenge"
				if 200 <= response.status_code < 300:
					setattr(tvspglobals, "WEBACCESS", True)
					return ""
				setattr(tvspglobals, "WEBACCESS", False)
				return str(response.status_code)
			finally:
				response.close()
		except exceptions.RequestException as err:
			setattr(tvspglobals, "WEBACCESS", False)
			return str(err)

	def get_html_data(self, url, params=None, timeout=(3.05, 6)):
		err_msg, htmldata = "", ""
		try:
			code, body = self.request(url, params, timeout=timeout)
			if 200 <= code < 300:
				htmldata = body.decode("utf-8", "replace")
			else:
				err_msg = f"Website access ERROR, HTTP status code: {code}"
		except Exception as error:
			err_msg = str(error)
			print(f"[{tvspglobals.MODULE_NAME}] ERROR in class 'TVcoreHelper:get_html_data': {err_msg}".replace("[__main__] ", ""))
		return err_msg, htmldata

	def get_binary_data(self, url, timeout=(3.05, 6)):
		err_msg, binary = "", None
		try:
			code, body = self.request(url, timeout=timeout)
			if 200 <= code < 300:
				binary = body
			else:
				err_msg = f"URL access ERROR, HTTP status code: {code}"
		except Exception as error:
			err_msg = str(error)
			print(f"[{tvspglobals.MODULE_NAME}] ERROR in class 'tvsphelper:get_binary_data': {url} - binary data could not be downloaded: {err_msg}".replace("[__main__] ", ""))
		return err_msg, binary

	def search_value(self, regex, text, fallback="", flags=None):
		text = search(regex, text, flags=flags) if flags else search(regex, text)
		return text.group(1) if text else fallback


tvsphelper = TVSparserHelper()


class TVSparserTips:
	def parse_tips(self, callback=None, passthrough=None):
		url = f"{tvspglobals.WEBURL}/tv-tipps/"
		err_msg, htmldata = tvsphelper.get_html_data(url)
		if err_msg:
			print(f"[{tvspglobals.MODULE_NAME}] ERROR in class 'TVSparserTips:parse_tips': {err_msg}".replace("[__main__] ", ""))
			return err_msg, ""
		extract = htmldata[htmldata.find('title="Spielfilme"'):]
		extract = extract[:extract.find('title="Top-bewertete Spielfilme"')]
		tips_dicts = []
		for entry in findall('<a href=(.*?)</a>', extract, S):
			entry = f"<a href={entry}</a>"  # add search patterns
			if tip_id := tvsphelper.search_value(r'data-id="BC(.*?)"', entry):
				title = unescape(tvsphelper.search_value(r'title="(.*?)"', entry))
				genre = tvsphelper.search_value(r'<span class="detail-genre">(.*?)</span>', entry)  # e.g. 'Katastrophenaction'
				category = tvsphelper.search_value(r'<span class="tips-teaser__top__category">(.*?)</span>', entry)  # e.g. 'SP' for 'Spielfilm'
				intro = tvsphelper.search_value(r'<div class="tips-teaser__bottom__intro">(.*?)</div>', entry)  # e.g. 'heute | 20:15 | ZDF'
				intros = intro.split(" | ") or []
				timestart_ts, hourmin = None, datetime.strptime(intros[1], '%H:%M').time() if len(intros) > 1 else None
				now_dt = datetime.now(tz=None)
				if hourmin and intros:
					if "heute" in intros[0]:
						timestart_ts = int(datetime.combine(now_dt, hourmin).timestamp())
					else:
						timestart_ts = int(datetime.combine(datetime.strptime(f"{intros[0]}{now_dt.year}", '%d.%m.%Y'), hourmin).timestamp())
				channel_name = intros[2] if len(intros) > 2 else ""
				timeinfos = intros[0] or ""
				timeinfos += f" | {intros[1]} Uhr" if len(intros) > 1 else ""
				rating = tvsphelper.search_value(r'<div class=\"tips-teaser__bottom__top-rating-text\">(.*?)</div>', entry).lower()
				# 'TOP BEWERTET': Highlights, wenn sie von der TVSpielfilm-Redaktion einen 'Daumen hoch'und eine IMDb-Bewertung von über 7,0 erhalten haben.
				is_top, thumb_id_numeric, imdb_rating = (True, 3, "TOP") if "top bewertet" in rating else (False, 0, "")
				is_tip = False  # unfortunately not supported at this time
				new = tvsphelper.search_value(r'<div class=\"tips-teaser__bottom__new-text\">(.*?)</div>', entry).lower()
				is_new = "neu" in new
				img_url = tvsphelper.search_value(r'<img src="([^"\r\n]*?)\?im=(?:Crop|Resize)', entry)
				if img_url:
					img_url += "?im=Resize,width=400"  # limit image to max. used size of 400 x ???
				channel_id = tvsphelper.search_value(r'<picture>\s*<img src=.*/(.*?).png', entry, flags=S).lower()
				asset_url = tvsphelper.search_value(r'<a href="(.*)"', entry)
				# not supported/used for the moment: timeendTs, country_year, titleLength, imdb_rating, fskText, conclusion, fsk
				tips_dicts.append({"tip_id": tip_id, "channel_name": channel_name, "channel_id": channel_id, "time_start": timestart_ts,
								"time_infos": timeinfos, "title": title, "genre": genre, "category": category,
								"imdb_rating": imdb_rating, "is_top": is_top, "is_tip": is_tip, "is_new": is_new,
								"thumb_id_numeric": thumb_id_numeric, "img_url": img_url, "asset_url": asset_url})
		if not callback:
			return "", tips_dicts
		if passthrough:
			callback(tips_dicts, passthrough)
			return "", {}
		callback(tips_dicts)


tvsptips = TVSparserTips()


class TVSparserChannels:
	def parse_channels(self, callback=None):
		url = f"{tvspglobals.WEBURL}{bytes.fromhex('2f73656e6465722ff'[:-1]).decode()}"
		err_msg, htmldata = tvsphelper.get_html_data(url)
		if err_msg:
			print(f"[{tvspglobals.MODULE_NAME}] ERROR in class 'TVSparserChannels:parse_channels': {err_msg}".replace("[__main__] ", ""))
			return []
		extract = htmldata[htmldata.find('<div class="channel-filter__channels-search">'):]
		extract = extract[:extract.find('<div class="channel-filter__submit"')]
		channel_dicts = []
		for cat_set in findall(r'<span class="name">(.*?)<picture>', extract, flags=S):
			channel_id = search(r'value="(.*?)"', cat_set)
			channel_id = channel_id.group(1).lower() if channel_id else ""
			category = search(r' data-group-name="(.*?)"', cat_set)
			# e.g. ['Hauptsender', 'Auslandssender', 'Spartensender', 'News und Dokus', 'Dritte Programme', 'Sportsender', 'Kindersender',
			# 'Musiksender', 'Shopping', 'Regionalsender', 'Sky Cinema', 'Pay TV', 'Sky Sport', 'Sky Entertainment']
			category = unescape(category.group(1)) if category else ""
			channel_dicts.append({"channel_id": channel_id, "category": category})
		if callback:
			callback(channel_dicts)
		return channel_dicts


tvspchannels = TVSparserChannels()


class TVSparserAssets:
	IMAGE_PARSER_VERSION = 2
	# also existing but not used: {"jetzt": "now", "gleich": "shortly", "abends": "primetips", "ganzer Tag": "day"}
	cat_filters = {"Spielfilm": "SP", "Serie": "SE", "Report": "RE", "Unterhaltung": "U", "Kinder": "KIN", "Sport": "SPO", "Andere": "AND"}
	channel_sets = {"Hauptsender": "g:1", "Dritte Programme": "g:2", "Sportsender": "g:8", "Spartensender ARD & ZDF": "g:4103125",
					"News und Doku": "g:11", "Kindersender": "g:10", "Ausland (deutschspr.)": "g:4", "Regionalsender": "g:3",
					"Musiksender": "g:9", "Spartensender": "g:3534866", "Shopping": "g:7", "Sky Cinema": "g:21", "Sky Sport": "g:13",
					"Sky Entertainment": "g:3738244", "Pay-TV": "g:19", "Auslandssender": "g:12"}

	def __init__(self):
		pass

	def get_channel_assets(self, channel_ids=[], curr_date_dt=None, time_code="day"):
		pages_list = []
		for channel_id in channel_ids:
			err_msg, assets_dicts = self.parse_channel_page(channel_id, curr_date_dt, time_code)
			if err_msg:
				print(f"[{tvspglobals.MODULE_NAME}] Error when parsing channel '{channel_id}': {err_msg}".replace("[__main__] ", "").replace("[__main__] ", ""))
			else:
				print(f"[{tvspglobals.MODULE_NAME}] Successfully parsed channel '{channel_id}'".replace("[__main__] ", "").replace("[__main__] ", ""))
				pages_list += assets_dicts
		return pages_list

	def parse_channel_page(self, channel_id, curr_date_dt, time_code="day", tips=None, categories=[]):
		def set_asset_key(key, value):
			if value:
				asset_dict[key] = value

		url = f"{tvspglobals.WEBURL}{bytes.fromhex('2f73756368652e68746d6c1'[:-1]).decode()}"
		curr_date_str = (datetime.now(tz=None) if time_code == "now" else curr_date_dt).strftime("%F")
		# In case desired time span is between 00:00 and 05:00, request data from previous day
		# Server philosophy: The server considers a day to be the period from 5:00 a.m. to 5:00 a.m. the following day.
		curr_date_int = int(f"{curr_date_dt.hour:02d}{curr_date_dt.minute:02d}")
		if curr_date_int >= 0 and curr_date_int < 500:  # correct timespan when next day (00:00h to 05:00h)
			curr_date_str = (curr_date_dt - timedelta(days=1)).strftime("%F")
		finish = False
		assets_dicts = []
		index, offset = 0, 0
		while True:
			params = {
					"offset": offset,  # offset=20 means assets #20...#39
					"filter": 1 if categories else None,  # None = all filters active |'1' = selected filters
					"order": None,  # sort order 'time' or 'channel', ('time' is default)
					"date": curr_date_str,  # e.g. '2025-05-18'
					"tips": tips,  # '1' = load tips only, ('None' is default)
					"cat[]": [self.cat_filters.get(cat_filter) for cat_filter in categories],   # e.g. ["Spielfilm", "Report"] -> ['SP', 'RE']
					"time": time_code or "prime",  # e.g. 'day', 'now', 'prime' or '22' etc.
					"channel": channel_id  # e.g. 'ARD' for 'Das Erste'
					}
			err_msg, htmldata = tvsphelper.get_html_data(url, params)
			if err_msg:
				print(f"[{tvspglobals.MODULE_NAME}] ERROR in class 'TVSparserAssets:parse_channel_page': {err_msg}".replace("[__main__] ", ""))
				return err_msg, []
			limit = 5 if time_code == "now" else 0  # 'Jetzt im TV' needs only the first 5 assets per channel
			extract = htmldata[htmldata.find('<div class="row component tv-tip-list">'):]
			extract = extract[:extract.find('<div class="row component category-select">')]
			for index, entry in enumerate(findall(r'<li class="tv-tip time-listing js-tv-show"(.*?)</li>', extract, S)):
				if limit and index > limit:
					finish = True
					break
				asset_dict = {}
				set_asset_key("id", tvsphelper.search_value(r'data-id="(.*?)"', entry))
				set_asset_key("title", unescape(tvsphelper.search_value(r'<span class="title">(.*?)</span>', entry)))
				genretime = tvsphelper.search_value(r'<span class="genre-time">(.*?)</span>', entry).split(" | ")
				set_asset_key("country_year", genretime[0].upper())  # e.g. 'SP' for 'Spielfilm'
				set_asset_key("genre", genretime[1] if len(genretime) > 1 else "")  # e.g. 'Katastrophenaction'
				itholder = tvsphelper.search_value(r'<div class="image-text-holder">(.*?)</a>', entry, flags=S)
				page_element = tvsphelper.search_value(r'"pageElementCreative":"(.*?)"', itholder, "", flags=S).split("|") if tvsphelper.search_value(r'"pageElementCreative":"(.*?)"', itholder, "", flags=S) else []
				channel_id = page_element[1].lower() if len(page_element) > 1 else ""
				set_asset_key("category", page_element[2].upper() if len(page_element) > 2 else "")
				thumb_id_numeric = tvsphelper.search_value(r'<span class="listing-icon rating-(.*?)"></span>', itholder, "0")
				set_asset_key("thumb_id_numeric", int(thumb_id_numeric) if thumb_id_numeric.isdigit() else 0)
				set_asset_key("is_top", False)  # not supported for the moment
				set_asset_key("is_tip", itholder.find('<span class="add-info icon-tip">TIPP</span>') > -1)
				set_asset_key("is_new", itholder.find('<span class="add-info icon-new">NEU</span>') > -1)
				set_asset_key("is_live", itholder.find('<span class="add-info icon-tip">Live</span>') > -1)
				set_asset_key("channel_id", channel_id)
				set_asset_key("channel_name", tvsphelper.search_value(r'<span class="c">(.*?)</span>', itholder))
				time_start_str = tvsphelper.search_value(r'data-start-time="(.*?)"', entry)
				time_start_ts = int(time_start_str) if time_start_str.isdigit() else 0
				time_end_str = tvsphelper.search_value(r'data-end-time="(.*?)"', entry)
				time_end_ts = int(time_end_str) if time_end_str.isdigit() else 0
				set_asset_key("time_start", f"{datetime.fromtimestamp(time_start_ts).isoformat()}+00:00")
				set_asset_key("time_end", f"{datetime.fromtimestamp(time_end_ts).isoformat()}+00:00")
				set_asset_key("asset_url", tvsphelper.search_value(r'<div class="image-text-holder">\s*<a href="(.*?)"', entry, flags=S))
				assets_dicts.append(asset_dict)
			if finish or extract.find('<span>Weitere Sendungen</span>') == -1:
				break
			data_ajax = tvsphelper.search_value(r'data-ajax=.*?offset=(.*?)&amp', extract, "", flags=S)
			if data_ajax and data_ajax.isdigit():
				offset = int(data_ajax)
			else:  # fallback
				offset += tvspglobals.LINES_PER_PAGE  # offset for the next ?? assets (=next page)
		return err_msg, assets_dicts

	def resolve_trailer_url(self, cid, lic):
		url = f"{bytes.fromhex('68747470733A2F2F6D656469612D6170692D70726F642E677265656E766964656F2E696F2F6170692F76312F636F6E74656E742F3'[:-1]).decode()}{cid}"
		headers = {
					"User-Agent": tvspglobals.USERAGENT,
					"Accept": "*/*",
					"Accept-Language": "de,en-US;q=0.7,en;q=0.3",
					"Connection": "keep-alive",
					bytes.fromhex("52656665726572E"[:-1]).decode(): bytes.fromhex("68747470733A2F2F7777772E7476737069656C66696C6D2E64652FE"[:-1]).decode(),
					bytes.fromhex("782D646C382D6C6963656E73656B65794"[:-1]).decode(): lic,
					bytes.fromhex("4F726967696EE"[:-1]).decode(): bytes.fromhex("68747470733A2F2F7777772E7476737069656C66696C6D2E64651"[:-1]).decode(),
					bytes.fromhex("5365632D4750433"[:-1]).decode(): bytes.fromhex("311"[:-1]).decode(),
					bytes.fromhex("5365632D46657463682D44657374A"[:-1]).decode(): bytes.fromhex("656D7074796"[:-1]).decode(),
					bytes.fromhex("5365632D46657463682D4D6F6465D"[:-1]).decode(): bytes.fromhex("636F72738"[:-1]).decode(),
					bytes.fromhex("5365632D46657463682D536974657"[:-1]).decode(): bytes.fromhex("63726F73732D73697465A"[:-1]).decode()
					}
		try:
			code, body = tvsphelper.request(url, headers=headers)
			if 200 <= code < 300:
				trailer_dicts = loads(body).get("result", {}).get("videoRenditions", {})
				return trailer_dicts[0].get("src", "") if trailer_dicts else ""
			else:
				print(f"[{tvspglobals.MODULE_NAME}] API server access ERROR, response code: {code}".replace("[__main__] ", ""))
		except Exception as error:
			err_msg = str(error)
			print(f"[{tvspglobals.MODULE_NAME}] ERROR in class 'TVSparserAssets:resolve_trailer_url': {err_msg}")
		return ""

	def parse_single_asset(self, asset_url):
		def set_asset_key(key, value):
			if value:
				asset_dict[key] = value

		err_msg, htmldata = tvsphelper.get_html_data(asset_url)
		if err_msg:
			print(f"[{tvspglobals.MODULE_NAME}] ERROR in class 'TVSparserAssets:parse_single_asset': {err_msg}".replace("[__main__] ", ""))
			return err_msg, {}
		extract = htmldata[htmldata.find('<div id="livetv-block"></div>'):]
		extract = extract[:extract.find(' <div class="tips-teaser__bottom-container"  >')]
		title = unescape(tvsphelper.search_value(r'<li class="swiper-slide">\s*<span><strong>(.*?)</strong></span>', extract, "", flags=S))
		country_year, genre = "", ""
		block = tvsphelper.search_value(r'<span class="text-row">(.*?)</span></div>', extract).split(" | ")
		if block and len(block) > 1:
			country_year = block[0]
			genre = block[1][:block[1].find("</span>")] if "</span>" in block[1] else block[1] if len(block) > 1 else ""
		repeat_hint = unescape(tvsphelper.search_value(r'<span class="text-row repeat">(.*?)</span>', f"{block[1].replace('</span>', '')}</span>")) if len(block) > 1 else ""
		serialinfo = tvsphelper.search_value(r'<section class="serial-info">\s*<span>(.*?)</span>', extract).split(",")
		len_serialinfo = len(serialinfo)
		season_number = serialinfo[0].replace("Staffel", "").strip() if len_serialinfo > 1 else ""
		episode_number = serialinfo[len_serialinfo - 1].replace("Folge", "").strip()
		category = tvsphelper.search_value(r'"epgCategory1" : "(.*?)",', htmldata).upper()  # e.g. "SP" for 'Spielfilm'
		conclusion = tvsphelper.search_value(r'<blockquote class="content-rating__rating-genre__conclusion-quote">(.*?)</blockquote>', extract, "", flags=S)
		conclusion = unescape(conclusion.replace("<p>", "").replace("</p>", "").strip())
		stage = tvsphelper.search_value(r'<span class="stage-underline gray">(.*?)</span>', extract, "", flags=S).split(" | ")
		time_start_end = stage[1].split(" - ") if len(stage) > 1 else ""
		time_start_ts, start_hour_min = None, datetime.strptime(time_start_end[0].replace(" Uhr", ""), '%H:%M').time() if time_start_end else None
		time_end_ts, end_hour_min = None, datetime.strptime(time_start_end[1].replace(" Uhr", ""), '%H:%M').time() if len(time_start_end) > 1 else None
		now_dt = datetime.now(tz=None)
		if "heute" in stage[0].lower():
			time_start_ts = int(datetime.combine(now_dt, start_hour_min).timestamp()) if start_hour_min else ""
			time_end_ts = int(datetime.combine(now_dt, end_hour_min).timestamp()) if end_hour_min else ""
		else:
			daydate = stage[0][stage[0].find(",") + 2:]  # convert 'Fr., 23.05.' -> '23.05.'
			time_start_ts = int(datetime.combine(datetime.strptime(f"{daydate}{now_dt.year}", '%d.%m.%Y'), start_hour_min).timestamp()) if start_hour_min else ""
			time_end_ts = int(datetime.combine(datetime.strptime(f"{daydate}{now_dt.year}", '%d.%m.%Y'), end_hour_min).timestamp()) if end_hour_min else ""
		channel_name = tvsphelper.search_value(r'class="button button--secondary js-track-link"\s*title="Tagesprogramm von (.*?)"', extract, "", flags=S)
		channel_id = tvsphelper.search_value(r'"pageElementCreative":"(.*?)"', extract).upper().replace("N\\/A", "").lower()
		img_url = ""
		for index, regex in enumerate(  # try some alternatives
			(
				r'--tv-detail ">\s*<img src="([^"\r\n]*?)\?im=(?:Crop|Resize)',
				r'<figure class=" default-image">\s*<img src="([^"\r\n]*?)\?im=(?:Crop|Resize)',
				r'streaming-video-teaser-image">\s*<img src="([^"\r\n]*?)\?im=(?:Crop|Resize)',
				r'bottom__background" style="background-image: url\(([^)"\r\n]*?)\?im=(?:Crop|Resize)'
			)
			):
			img_url = tvsphelper.search_value(regex, extract, "", flags=S)
			if img_url:
				img_url += "?im=Resize,width=400"  # limit image to max. used size of 400 x ???
				break
		img_credits = tvsphelper.search_value(r'<span class="credit">(.*?)</span>', extract)
		access = tvsphelper.search_value(r'<script src="(.*?)"></script>', extract)
		access = access[access.find("key=") + 4:] if access else ""
		cid = tvsphelper.search_value(r'content-id="(.*?)"', extract)
		lic = tvsphelper.search_value(r'data-license-key="(.*?)"', extract)
		trailer_data = f"{cid},{lic}" if cid and lic else ""
		descblock = tvsphelper.search_value(r'<section class="broadcast-detail__description">(.*?)</section>', extract, "", flags=S)
		preview = tvsphelper.search_value(r'<p class="headline">(.*?)</p>', descblock)
		text = unescape(tvsphelper.search_value(r'<p>(.*?)</p>', descblock)).replace("<br/>", "\n").replace('&amp;', '&')
		infoblock = tvsphelper.search_value(r'<p class="headline">Infos</p>(.*?)<p class="headline headline--spacing">', extract, "", flags=S)
		infodict = dict(findall(r'<dt>(.*?)</dt>\s*<dd>(.*?)</dd>', infoblock, flags=S))
		country, first_year, length = infodict.get("Land", ""), infodict.get("Jahr", ""), infodict.get("Länge", "")
		fsk = infodict.get("FSK", "-1")[:2]
		fsk = int(fsk) if fsk.isdigit() else -1
		crewblock = tvsphelper.search_value(r'<p class="headline headline--spacing">Crew</p>(.*?)</div>', extract, "", flags=S)
		crewblock = findall(r'<dt>(.*?)</dt>\s*<dd>\s*(.*?)\s*</dd>', crewblock, flags=S)
		crewdict = {}
		crewdict["crew"] = {}
		for crew in crewblock:
			role = unescape(crew[0].strip())
			name = unescape(tvsphelper.search_value(r'title="(.*?)"', crew[1])) or unescape(crew[1].strip())
			namelist = crewdict.get(role, [])
			namelist.append(name)
			crewdict["crew"][role] = namelist
		castblock = tvsphelper.search_value(r'<p class="headline">Cast</p>(.*?)</dl>', extract, "", flags=S)
		castblock = findall(r'<dt>(.*?)</dt>\s*<dd>\s*(.*?)\s*</dd>', castblock, flags=S)
		castdict = {}
		castdict["cast"] = {}
		for casting in castblock:
			role = unescape(casting[0].strip())
			name = unescape(tvsphelper.search_value(r'title="(.*?)"', casting[1])) or unescape(casting[1].strip())
			namelist = castdict.get(role, [])
			namelist.append(name)
			castdict["cast"][role] = namelist
		thumb_id_numeric = tvsphelper.search_value(r'<div class="content-rating__rating-genre__thumb rating-(.*?)"></div>', extract, "0")
		thumb_id_numeric = int(thumb_id_numeric) if thumb_id_numeric.isdigit() else 0
		is_top = "TOP BEWERTET" in tvsphelper.search_value(r'<div class="content-rating__top-rated">(.*?)</div>', extract).upper()
		is_tip, is_new, is_live = False, False, False
		addinfo = tvsphelper.search_value(r'<ul class="set-list">(.*?)</ul>', extract)
		for info in findall(r'<span class="add-info icon-tip nodistance">(.*?)</span>', addinfo):
			is_tip = True if not is_tip and "TIPP" in info else is_tip
			is_new = True if not is_new and "NEU" in info else is_new
			is_live = True if not is_live and "LIVE" in info else is_live
		ratingblock = tvsphelper.search_value(r'<ul class="content-rating__rating-genre__list">(.*?)</ul>', extract, "", flags=S)
		ratingblock = findall(r'<li class="content-rating__rating-genre__list-item">(.*?)</li>', ratingblock, flags=S)
		ratingdict = {}
		for rating in ratingblock:
			label = tvsphelper.search_value(r'<span class="content-rating__rating-genre__list-item__label">(.*?)</span>', rating)
			label = label.replace("Humor", "ratingHumor").replace("Anspruch", "ratingDemanding").replace("Action", "ratingAction").replace("Spannung", "ratingSuspense").replace("Erotik", "ratingErotic")
			rating = tvsphelper.search_value(r'class="content-rating__rating-genre__list-item__rating rating-(.*?)">', rating, "-1")
			rating = int(rating) if rating.isdigit() else -1
			ratingdict[label] = rating
		imdb_rating = tvsphelper.search_value(r'<div class="content-rating__imdb-rating__rating-value">(.*?)</div>', extract)
		asset_dict = {}
		set_asset_key("title", title)
		set_asset_key("category", category)
		set_asset_key("genre", genre)
		set_asset_key("length", length)
		set_asset_key("country_year", country_year)
		set_asset_key("first_year", first_year)
		set_asset_key("country", country)
		set_asset_key("preview", preview)
		set_asset_key("text", text)
		set_asset_key("conclusion", conclusion)
		set_asset_key("thumb_id_numeric", thumb_id_numeric)
		set_asset_key("is_top", is_top)
		set_asset_key("is_tip", is_tip)
		set_asset_key("is_new", is_new)
		set_asset_key("is_live", is_live)
		asset_dict.update(ratingdict)
		set_asset_key("imdb_rating", imdb_rating)
		asset_dict.update(crewdict)
		asset_dict.update(castdict)
		set_asset_key("fsk", fsk)
		set_asset_key("img_url", img_url)
		set_asset_key("image_parser_version", self.IMAGE_PARSER_VERSION)
		set_asset_key("img_credits", img_credits)
		set_asset_key("trailer_data", trailer_data)
		set_asset_key("channel_id", channel_id)
		set_asset_key("channel_name", channel_name)
		set_asset_key("season_number", season_number)
		set_asset_key("episode_number", episode_number)
		set_asset_key("repeat_hint", repeat_hint)
		set_asset_key("time_start", f"{datetime.fromtimestamp(time_start_ts).isoformat()}+00:00" if time_start_ts else "")
		set_asset_key("time_end", f"{datetime.fromtimestamp(time_end_ts).isoformat()}+00:00" if time_end_ts else "")
		set_asset_key("asset_url", asset_url)
		return err_msg, asset_dict


tvspassets = TVSparserAssets()


def main(argv):  # shell interface
	filename = ""
	json_list = []
	helpstring = "tvsparser v1.0: try 'python tvsparser.py -h' for more information"
	try:
		opts, args = getopt(argv, "j:anchst", ["json=", "assetslist=", "now", "channellist=", "help", "singleasset=", "tipslist="])
	except GetoptError as error:
		print(f"Error: {error}\n{helpstring}")
		exit(2)
	for opt, arg in opts:
		opt = opt.lower().strip()
		arg = arg.strip()
		if not opts or opt == "-h":
			print("Usage 'tvsparser v1.0': python tvsparser.py [option...] <data>\n"
			"-a, --assetslist\tget list of assets of a channel (details: see code)\n"
			"-n, --now\t\tget list of assets of currently running programs (details: see code)\n"
			"-c, --channellist\tget list of all supported channels\n"
			"-h, --help\t\tget an overview of the options\n"
			"-s, --single\t\tget a single asset (url: see code)\n"
			"-t, --tipslist\t\tget all TV-tips\n"
			"-j, --json <filename>\tFile output formatted in JSON\n")
			exit()
		elif opt in ("-a", "--assetslist"):
			json_list = tvspassets.get_channel_assets(["ARD"], datetime.now(), "day")
		elif opt in ("-n", "--now"):
			json_list = tvspassets.get_channel_assets(["ARD"], datetime.now(), "now")
		elif opt in ("-c", "--channellist"):
			json_list = tvspchannels.parse_channels()
		elif opt in ("-j", "--json"):
			filename = arg
		elif opt in ("-s", "--single"):
			err_msg, json_list = tvspassets.parse_single_asset("https://m.tvspielfilm.de/tv-programm/sendung/sommer-spass-mit-andy-borg-2026,6a5877b237d24c784f13f435.html")
			if err_msg:
				print(f"Error parsing asset: {err_msg}")
		elif opt in ("-t", "--tipslist"):
			json_list = tvsptips.parse_tips()
	if json_list and filename:
		with open(filename, "w") as file:
			dump(json_list, file)
		print(f"JSON file '{filename}' was successfully created.")


if __name__ == "__main__":
	# the fetches are blocking calls from a worker thread into the reactor, so main() runs in a thread
	reactor.callWhenRunning(lambda: deferToThread(main, argv[1:]).addBoth(lambda result: reactor.stop()))
	reactor.run()
