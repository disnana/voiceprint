# pip install httpx mutagen
from enum import Enum

import httpx
import mimetypes
import wave
import io
import os
from mutagen.mp3 import MP3

# WAV検証
def check_wav(file_path):
    try:
        with wave.open(file_path, 'rb') as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            comptype = wf.getcomptype()
            if channels != 2:
                return False, "WAV: チャンネル数が2（ステレオ）ではありません"
            if sampwidth != 2:
                return False, "WAV: サンプルサイズが16ビット（2バイト）ではありません"
            if framerate != 48000:
                return False, "WAV: サンプリング周波数が48kHz（48000Hz）ではありません"
            if comptype != 'NONE':
                return False, "WAV: リニアPCM形式（非圧縮）以外は非対応"
        return True, "OK"
    except Exception as e:
        return False, f"WAV: ファイル形式として不正または破損: {str(e)}"

# MP3検証
def check_mp3(file_path):
    try:
        info = MP3(file_path)
        channels = info.info.channels
        framerate = int(info.info.sample_rate)
        bitrate = int(info.info.bitrate) // 1000  # kbps
        vbr = info.info.bitrate_mode != 0
        if channels != 2:
            return False, "MP3: チャンネル数が2（ステレオ）ではありません"
        if framerate != 48000:
            return False, f"MP3: サンプリング周波数が48kHz（48000Hz）ではありません"
        if bitrate < 192:
            return False, f"MP3: ビットレートが192kbps未満（{bitrate}kbps）です"
        # VBRは許容（推奨はCBR）
        return True, "OK" if not vbr else "VBRですが許容（推奨はCBR）"
    except Exception as e:
        return False, f"MP3: ファイル形式として不正または破損: {str(e)}"

# 自動でMIMEタイプを判別
def guess_mime_type(filename):
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"

class ModeFiles(Enum):
    NESTED = "dict"
    LIST = "list"

class Mode:
    files = ModeFiles

class ApiClientSync:
    Mode = Mode
    def __init__(self, api_key: str, url="https://voiceprint.disnana.com/api", auto_refresh=False):
        self.url = url
        self.api_key = api_key
        self.session = None
        self.token = None
        self.refresh_token = None
        self.headers = {"Authorization": f"Bearer {self.api_key}"}
        self.client = httpx.Client()
        self.auto_refresh_session = auto_refresh
        self.VoiceprintService = self.VoiceprintService(self)


    class VoiceprintService:
        def __init__(self, new_self):
            self.base_class = new_self

        def files(self, target_name: str = None, mode=Mode.files.NESTED):
            url = f"{self.base_class.url}/files"
            params = {"target_name": target_name} if target_name else {}
            params["mode"] = mode.value
            response = self.base_class.get_data(url, headers=self.base_class.headers, params=params)
            return response

        def upload(self, target_name:str, file_path: str, filename:str = None):
            if filename is None:
                filename = os.path.basename(file_path)
            url = f"{self.base_class.url}/upload"
            # 拡張子判定
            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".wav":
                valid, reason = check_wav(file_path)
                if not valid:
                    print(f"検証エラー: {reason}")
                    return
            elif ext == ".mp3":
                valid, reason = check_mp3(file_path)
                if not valid:
                    print(f"検証エラー: {reason}")
                    return
            else:
                print("wavかmp3のみ対応です")
                return

            # ファイルサイズチェック
            if os.path.getsize(file_path) > 10 * 1024 * 1024:
                print("ファイルサイズが10MBを超えています")
                return

            # 自動でMIMEタイプを指定
            mime_type = guess_mime_type(file_path)

            with open(file_path, 'rb') as f:
                files = {
                    "file": (filename, f.read(), mime_type)
                }
                # post_dataはあなたのクラスインスタンスのメソッド
                response = self.base_class.post_data(url, files=files, headers=self.base_class.headers, data={"target_name": target_name})
                print(response)

    def close(self):
        self.client.close()

    def login(self, no_error=False):
        url = f"{self.url}/login"
        response = self.get_data(url, headers=self.headers, no_error=no_error)
        if response is None:
            return None
        if "token" in response:
            self.token = response["token"]
        if "refresh_token" in response:
            self.refresh_token = response["refresh_token"]
        return response

    def logout(self):
        url = f"{self.url}/logout"
        response = self.post_data(url, headers=self.headers)
        if response is None:
            raise ApiClientAuthError("ログアウト失敗")
        self.token = None
        self.refresh_token = None
        return response

    def refresh(self):
        url = f"{self.url}/refresh"
        response = self.post_data(url, headers=self.headers, json={"refresh_token": self.refresh_token})
        if response is None:
            return None
        if "token" in response:
            self.token = response["token"]
        if "refresh_token" in response:
            self.refresh_token = response["refresh_token"]
        return response

    def get_data(self, url, headers=None, params=None, timeout=10, no_error=False):
        retly = False
        while True:
            response = self.client.get(url, headers=headers, params=params, timeout=timeout, cookies=self.session)
            if response.status_code == 200:
                self.session = response.cookies
                return response.json()
            elif response.status_code == 403:
                if self.auto_refresh_session:
                    if retly:
                        raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")
                    refreshed = self._refresh_session()
                    if not refreshed:
                        logged_in = self._do_login()
                        if not logged_in:
                            raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")
                    if url == f"{self.url}/refresh":
                        return None
                    retly = True
                    continue
                if no_error:
                    return None
                else:
                    raise ApiClientHTTPError(response.status_code, response.text)
            else:
                if no_error:
                    return None
                else:
                    raise ApiClientHTTPError(response.status_code, response.text)

    def post_data(self, url, headers=None, params=None, timeout=10, content=None, files=None, data=None, json=None, no_error=False):
        retly = False
        while True:
            response = self.client.post(url, headers=headers, params=params, timeout=timeout, cookies=self.session, content=content, files=files, data=data, json=json)
            if response.status_code == 200:
                self.session = response.cookies
                return response.json()
            elif response.status_code == 403:
                if self.auto_refresh_session:
                    if retly:
                        raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")
                    refreshed = self._refresh_session()
                    if not refreshed:
                        logged_in = self._do_login()
                        if not logged_in:
                            raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")
                    if url == f"{self.url}/refresh":
                        return None
                    retly = True
                    continue
                if no_error:
                    return None
                else:
                    raise ApiClientHTTPError(response.status_code, response.text)
            else:
                if no_error:
                    return None
                else:
                    raise ApiClientHTTPError(response.status_code, response.text)

    def auto_refresh(self):
        if self.refresh_token:
            response = self.post_data(f"{self.url}/refresh", headers=self.headers, json={"refresh_token": self.refresh_token}, no_error=True)
            if response is None:
                resp = self.login(no_error=True)
                if resp is None:
                    raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")
                return resp
            if "token" in response:
                self.token = response["token"]
            if "refresh_token" in response:
                self.refresh_token = response["refresh_token"]
            if response is not None:
                if "token" in response and "refresh_token" in response:
                    return response
            raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")
        else:
            response = self.login(no_error=True)
            if response is not None:
                if "token" in response and "refresh_token" in response:
                    return response
            raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")

    def _refresh_session(self):
        url = f"{self.url}/refresh"
        response = self.client.post(url, headers=self.headers, json={"refresh_token": self.refresh_token})
        data = response.json() if response.status_code == 200 else None
        if data and "token" in data and "refresh_token" in data:
            self.token = data["token"]
            self.refresh_token = data["refresh_token"]
            return True
        return False

    def _do_login(self):
        url = f"{self.url}/login"
        response = self.client.get(url, headers=self.headers)
        data = response.json() if response.status_code == 200 else None
        if data and "token" in data and "refresh_token" in data:
            self.token = data["token"]
            self.refresh_token = data["refresh_token"]
            return True
        return False


class ApiClientAsync:
    Mode = Mode
    def __init__(self, api_key: str, url="https://voiceprint.disnana.com/api", auto_refresh=False):
        self.url = url
        self.api_key = api_key
        self.session = None
        self.token = None
        self.refresh_token = None
        self.headers = {"Authorization": f"Bearer {self.api_key}"}
        self.client = httpx.AsyncClient()
        self.auto_refresh_session = auto_refresh
        self.VoiceprintService = self.VoiceprintService(self)

    class VoiceprintService:
        def __init__(self, new_self):
            self.base_class = new_self

        async def files(self, target_name: str = None, mode=Mode.files.NESTED):
            url = f"{self.base_class.url}/files"
            params = {"target_name": target_name} if target_name else {}
            params["mode"] = mode.value
            response = await self.base_class.get_data(url, headers=self.base_class.headers, params=params)
            return response

        async def upload(self, target_name: str, file_path: str, filename: str = None):
            if filename is None:
                filename = os.path.basename(file_path)
            url = f"{self.base_class.url}/upload"
            # 拡張子判定
            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".wav":
                valid, reason = check_wav(file_path)
                if not valid:
                    print(f"検証エラー: {reason}")
                    return
            elif ext == ".mp3":
                valid, reason = check_mp3(file_path)
                if not valid:
                    print(f"検証エラー: {reason}")
                    return
            else:
                print("wavかmp3のみ対応です")
                return

            # ファイルサイズチェック
            if os.path.getsize(file_path) > 10 * 1024 * 1024:
                print("ファイルサイズが10MBを超えています")
                return

            # 自動でMIMEタイプを指定
            mime_type = guess_mime_type(file_path)

            with open(file_path, 'rb') as f:
                files = {
                    "file": (filename, f.read(), mime_type)
                }
                # post_dataはあなたのクラスインスタンスのメソッド
                response = await self.base_class.post_data(url, files=files, headers=self.base_class.headers,
                                                     data={"target_name": target_name})
            return response

    async def close(self):
        await self.client.aclose()

    async def login(self, no_error=False):
        url = f"{self.url}/login"
        response = await self.get_data(url, headers=self.headers, no_error=no_error)
        if response is None:
            return None
        if "token" in response:
            self.token = response["token"]
        if "refresh_token" in response:
            self.refresh_token = response["refresh_token"]
        return response

    async def logout(self):
        url = f"{self.url}/logout"
        response = await self.post_data(url, headers=self.headers, no_error=True)
        if response is None:
            return None
        self.token = None
        self.refresh_token = None
        return response

    async def refresh(self):
        url = f"{self.url}/refresh"
        response = await self.post_data(url, headers=self.headers, json={"refresh_token": self.refresh_token})
        if response is None:
            return None
        if "token" in response:
            self.token = response["token"]
        if "refresh_token" in response:
            self.refresh_token = response["refresh_token"]
        return response

    async def get_data(self, url, headers=None, params=None, timeout=10, no_error=False):
        retly = False
        while True:
            response = await self.client.get(url, headers=headers, params=params, timeout=timeout, cookies=self.session)
            if response.status_code == 200:
                self.session = response.cookies
                return response.json()
            elif response.status_code == 403:
                if self.auto_refresh_session:
                    if retly:
                        raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")
                    refreshed = await self._refresh_session()
                    if not refreshed:
                        logged_in = await self._do_login()
                        if not logged_in:
                            raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")
                    retly = True
                    continue
                if no_error:
                    return None
                else:
                    raise ApiClientHTTPError(response.status_code, response.text)
            else:
                if no_error:
                    return None
                else:
                    raise ApiClientHTTPError(response.status_code, response.text)

    async def post_data(self, url, headers=None, params=None, timeout=10, content=None, files=None, data=None, json=None, no_error=False):
        retly = False
        while True:
            response = await self.client.post(url, headers=headers, params=params, timeout=timeout, cookies=self.session, content=content, files=files, data=data, json=json)
            if response.status_code == 200:
                self.session = response.cookies
                return response.json()
            elif response.status_code == 403:
                if self.auto_refresh_session:
                    if retly:
                        raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")
                    refreshed = await self._refresh_session()
                    if not refreshed:
                        logged_in = await self._do_login()
                        if not logged_in:
                            raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")
                    retly = True
                    continue
                if no_error:
                    return None
                else:
                    raise ApiClientHTTPError(response.status_code, response.text)
            else:
                if no_error:
                    return None
                else:
                    raise ApiClientHTTPError(response.status_code, response.text)

    async def auto_refresh(self):
        if self.refresh_token:
            response = await self.post_data(f"{self.url}/refresh", headers=self.headers, json={"refresh_token": self.refresh_token}, no_error=True)
            if response is None:
                resp = await self.login(no_error=True)
                if resp is None:
                    raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")
                return resp
            if "token" in response:
                self.token = response["token"]
            if "refresh_token" in response:
                self.refresh_token = response["refresh_token"]
            if response is not None:
                if "token" in response and "refresh_token" in response:
                    return response
            raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")
        else:
            response = await self.login(no_error=True)
            if response is not None:
                if "token" in response and "refresh_token" in response:
                    return response
            raise ApiClientTokenExpired("トークンの有効期限切れ・無効時")

    async def _refresh_session(self):
        url = f"{self.url}/refresh"
        response = await self.client.post(url, headers=self.headers, json={"refresh_token": self.refresh_token})
        data = response.json() if response.status_code == 200 else None
        if data and "token" in data and "refresh_token" in data:
            self.token = data["token"]
            self.refresh_token = data["refresh_token"]
            return True
        return False

    async def _do_login(self):
        url = f"{self.url}/login"
        response = await self.client.get(url, headers=self.headers)
        data = response.json() if response.status_code == 200 else None
        if data and "token" in data and "refresh_token" in data:
            self.token = data["token"]
            self.refresh_token = data["refresh_token"]
            return True
        return False

class ApiClient:
    def __new__(cls, api_key: str, use_async=False, url="https://voiceprint.disnana.com/api", auto_refresh=False):
        if use_async:
            return ApiClientAsync(api_key, url, auto_refresh)
        else:
            return ApiClientSync(api_key, url, auto_refresh)

class ApiClientError(Exception):
    """APIクライアントの基底例外"""

class ApiClientHTTPError(ApiClientError):
    def __init__(self, status_code, message=None):
        super().__init__(f"API HTTP Error: {status_code} {message or ''}")
        self.status_code = status_code
        self.message = message

class ApiClientAuthError(ApiClientError):
    """認証失敗・ログイン・ログアウト失敗"""
    pass

class ApiClientTokenExpired(ApiClientAuthError):
    """トークンの有効期限切れ・無効時"""
    pass

class ApiClientAccountBanned(ApiClientAuthError):
    """BANされたなど特別な認証拒否"""
    pass

class TokenUpdateError(ApiClientError):
    """自動トークン更新の失敗（auto_refresh用）"""
    pass
