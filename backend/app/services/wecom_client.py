"""企业微信 API 客户端。

封装企业微信（WeChat Work / WeCom）服务端 API 调用，
自动管理 access_token 的获取与刷新。

文档参考: https://developer.work.weixin.qq.com/document/
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

# 企微 API 错误码
_ERRCODE_TOKEN_EXPIRED = 42001
_ERRCODE_TOKEN_INVALID = 40014
_ERRCODE_SUCCESS = 0

# token 提前刷新缓冲（秒）
_TOKEN_REFRESH_BUFFER = 300  # 5 分钟


class WeComAPIError(Exception):
    """企业微信 API 调用错误。"""

    def __init__(self, errcode: int, errmsg: str):
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"WeCom API error {errcode}: {errmsg}")


class WeChatWorkClient:
    """企业微信 API 客户端。

    用法:
        client = WeChatWorkClient(corpid="ww...", secret="...")
        try:
            contacts = await client.list_external_contacts()
        finally:
            await client.close()
    """

    BASE_URL = "https://qyapi.weixin.qq.com/cgi-bin"

    def __init__(self, corpid: str, secret: str):
        self.corpid = corpid
        self.secret = secret
        self._access_token: str | None = None
        self._token_expires_at: float = 0
        self._http = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,
        )

    # ── access_token 管理 ──────────────────────────────────────

    async def get_access_token(self) -> str:
        """获取并缓存 access_token，自动刷新。

        在距离过期还剩 5 分钟时就提前刷新，避免请求途中过期。
        """
        if self._access_token and time.time() < self._token_expires_at - _TOKEN_REFRESH_BUFFER:
            return self._access_token

        resp = await self._http.get(
            "/gettoken",
            params={"corpid": self.corpid, "corpsecret": self.secret},
        )
        resp.raise_for_status()
        data = resp.json()

        errcode = data.get("errcode", _ERRCODE_SUCCESS)
        if errcode != _ERRCODE_SUCCESS:
            raise WeComAPIError(errcode, data.get("errmsg", "unknown error"))

        self._access_token = data["access_token"]
        expires_in = data.get("expires_in", 7200)
        self._token_expires_at = time.time() + expires_in

        logger.debug("access_token 获取成功，有效期 %ds", expires_in)
        return self._access_token

    # ── 通用请求方法 ───────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        retry_on_token_error: bool = True,
    ) -> dict:
        """发送 API 请求，自动附带 access_token 并处理 token 过期重试。"""
        token = await self.get_access_token()
        request_params = {"access_token": token, **(params or {})}

        resp = await self._http.request(
            method,
            path,
            params=request_params,
            json=json,
        )
        resp.raise_for_status()
        data = resp.json()

        errcode = data.get("errcode", _ERRCODE_SUCCESS)
        # token 过期/失效时自动刷新重试一次
        if retry_on_token_error and errcode in (_ERRCODE_TOKEN_EXPIRED, _ERRCODE_TOKEN_INVALID):
            logger.info("access_token 已过期（errcode=%d），自动刷新重试", errcode)
            self._access_token = None
            self._token_expires_at = 0
            return await self._request(method, path, params=params, json=json, retry_on_token_error=False)

        return data

    def _check_error(self, data: dict) -> None:
        """检查 API 返回是否包含错误，有则抛出 WeComAPIError。"""
        errcode = data.get("errcode", _ERRCODE_SUCCESS)
        if errcode != _ERRCODE_SUCCESS:
            raise WeComAPIError(errcode, data.get("errmsg", "unknown error"))

    # ── 外部联系人管理 ─────────────────────────────────────────

    async def list_external_contacts(self, cursor: str = "", limit: int = 100) -> dict:
        """获取外部联系人列表。

        返回:
            {
                "external_userid_list": ["...", "..."],
                "next_cursor": "..."
            }
        """
        data = await self._request(
            "POST",
            "/externalcontact/list",
            json={"cursor": cursor, "limit": limit},
        )
        self._check_error(data)
        return data

    async def get_external_contact(self, external_userid: str) -> dict:
        """获取外部联系人详情。

        返回:
            {
                "external_contact": {
                    "external_userid": "...",
                    "name": "...",
                    "position": "...",
                    "avatar": "...",
                    "corp_name": "...",
                    "corp_full_name": "...",
                    "type": 1,
                    "gender": 1,
                    "unionid": "...",
                    ...
                },
                "follow_up_user": [...]
            }
        """
        data = await self._request(
            "GET",
            "/externalcontact/get",
            params={"external_userid": external_userid},
        )
        self._check_error(data)
        return data

    async def batch_get_external_contacts_by_phone(
        self,
        phone_list: list[str],
        cursor: str = "",
        limit: int = 100,
    ) -> list[dict]:
        """通过手机号批量查询外部联系人。

        企业微信没有直接按手机号查联系人的 API，
        因此采用遍历 list + get 并在本地按手机号匹配的方案。
        如果需要更高效的方式，可考虑在企业微信后台按手机号导出。

        Args:
            phone_list: 需要匹配的手机号列表
            cursor: 分页游标（内部递归使用）
            limit: 每页拉取数量

        Returns:
            匹配到的外部联系人详情列表
        """
        matched: list[dict] = []
        phone_set = set(phone_list)

        page = await self.list_external_contacts(cursor=cursor, limit=limit)
        userid_list = page.get("external_userid_list", [])

        for uid in userid_list:
            try:
                detail = await self.get_external_contact(uid)
                # 外部联系人的手机号在 follow_up_user 的 remark_mobiles 中
                follow_users = detail.get("follow_up_user", [])
                contact_phones: set[str] = set()
                for fu in follow_users:
                    remark_mobiles = fu.get("remark_mobiles", [])
                    contact_phones.update(remark_mobiles)

                if contact_phones & phone_set:
                    matched.append(detail)
            except WeComAPIError:
                logger.warning("获取外部联系人 %s 详情失败，跳过", uid)
                continue

        # 递归处理下一页
        next_cursor = page.get("next_cursor", "")
        if next_cursor:
            rest = await self.batch_get_external_contacts_by_phone(
                phone_list=phone_list,
                cursor=next_cursor,
                limit=limit,
            )
            matched.extend(rest)

        return matched

    async def update_external_contact(self, external_userid: str, data: dict) -> dict:
        """更新外部联系人信息。

        注意：此接口实际可更新的字段有限，具体以企微文档为准。
        通常通过 add_external_contact_remark 来更新备注相关字段。

        Args:
            external_userid: 外部联系人 ID
            data: 需要更新的字段

        Returns:
            API 返回数据
        """
        body = {"external_userid": external_userid, **data}
        resp = await self._request(
            "POST",
            "/externalcontact/update",
            json=body,
        )
        self._check_error(resp)
        return resp

    async def add_external_contact_remark(
        self,
        external_userid: str,
        remark: str,
    ) -> dict:
        """添加/修改外部联系人备注。

        需要通过「跟进人」(follow_up_user) 来操作，
        此方法简化为更新备注字段。

        Args:
            external_userid: 外部联系人 ID
            remark: 备注内容

        Returns:
            API 返回数据
        """
        body = {
            "external_userid": external_userid,
            "remark": remark,
        }
        resp = await self._request(
            "POST",
            "/externalcontact/add_external_contact_remark",
            json=body,
        )
        self._check_error(resp)
        return resp

    # ── 企业标签管理 ───────────────────────────────────────────

    async def get_corp_tag_list(self, tag_id: list[str] | None = None) -> dict:
        """获取企业标签库。

        Args:
            tag_id: 要查询的标签 ID 列表，为空则返回全部

        Returns:
            {
                "tag_group": [
                    {
                        "group_id": "...",
                        "group_name": "...",
                        "tag": [{"id": "...", "name": "...", ...}],
                        ...
                    }
                ]
            }
        """
        body = {"tag_id": tag_id or []}
        resp = await self._request(
            "POST",
            "/externalcontact/get_corp_tag_list",
            json=body,
        )
        self._check_error(resp)
        return resp

    async def edit_corp_tag(self, id: str, name: str) -> dict:
        """编辑企业客户标签。

        Args:
            id: 标签 ID
            name: 新标签名称

        Returns:
            API 返回数据
        """
        body = {"id": id, "name": name}
        resp = await self._request(
            "POST",
            "/externalcontact/edit_corp_tag",
            json=body,
        )
        self._check_error(resp)
        return resp

    async def mark_external_contact(
        self,
        external_userid: str,
        add_tag: list[str] | None = None,
        remove_tag: list[str] | None = None,
    ) -> dict:
        """为外部联系人打标签/移除标签。

        Args:
            external_userid: 外部联系人 ID
            add_tag: 要添加的标签 ID 列表
            remove_tag: 要移除的标签 ID 列表

        Returns:
            API 返回数据
        """
        body = {
            "external_userid": external_userid,
            "add_tag": add_tag or [],
            "remove_tag": remove_tag or [],
        }
        resp = await self._request(
            "POST",
            "/externalcontact/mark",
            json=body,
        )
        self._check_error(resp)
        return resp

    # ── 生命周期 ───────────────────────────────────────────────

    async def close(self) -> None:
        """关闭 HTTP 客户端，释放连接。"""
        await self._http.aclose()
