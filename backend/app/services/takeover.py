"""既有码接管业务服务。

这里集中维护评估、导入、旧码解析、外部核验和路由版本状态机。
路由层只负责权限、输入和 HTTP 编排；所有消费者结果都从本模块的事实计算得出。
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import socket
import ssl
import uuid
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlparse

from fastapi import HTTPException, status
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.code import CodeBatch, CodeItem
from app.models.product import SKU, ProductionBatch
from app.models.takeover import (
    TakeoverAlias,
    TakeoverAliasStatus,
    TakeoverAliasType,
    TakeoverCutoverEvent,
    TakeoverDomainCheck,
    TakeoverImportError,
    TakeoverImportJob,
    TakeoverImportStatus,
    TakeoverMode,
    TakeoverObservation,
    TakeoverProject,
    TakeoverProjectStatus,
    TakeoverRouteStatus,
    TakeoverRouteVersion,
)
from app.services.audit import write_audit_log
from app.services.entitlement import PLAN_EXPIRED_CODE, PLAN_EXPIRED_DETAIL, TenantPlanExpiredError

logger = logging.getLogger(__name__)

DEFAULT_CNAME_TARGET = "cname.yimatong.cn"
TAKEOVER_IMPORT_QUEUE_KEY = "ymt:takeover_import:queue"
TAKEOVER_IMPORT_PLAN_EXPIRED_ERROR = f"{PLAN_EXPIRED_CODE}: {PLAN_EXPIRED_DETAIL}"
MAX_IMPORT_ROWS = 100_000
MAX_IMPORT_BYTES = 10 * 1024 * 1024
SUPPORTED_IMPORT_COLUMNS = {
    "legacy_code",
    "internal_public_id",
    "product_code",
    "sku_code",
    "batch_code",
    "distributor_code",
    "region_code",
    "code_type",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().lower().rstrip(".")


def normalize_legacy_code(value: str) -> str:
    return " ".join(value.strip().split()).upper()


def _host(url: str) -> str | None:
    parsed = urlparse(url)
    return _normalize_domain(parsed.hostname)


def _safe_url(url: str, *, allow_query: bool = True) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="只支持有效的 HTTP(S) 链接")
    if not allow_query and parsed.query:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="该链接不允许携带 query 参数")
    return url


def _check(key: str, label: str, passed: bool, detail: str, *, blocking: bool = True) -> dict:
    return {"key": key, "label": label, "passed": passed, "detail": detail, "blocking": blocking}


def assess_project_inputs(
    *,
    mode: str,
    sample_url: str,
    url_rule: dict,
    control_facts: dict,
    source_domain: str | None,
    consumer_domain: str | None,
    fallback_url: str,
) -> dict:
    """基于输入事实给出可解释的能力矩阵，不把固定链接冒充一物一码。"""
    parsed = urlparse(sample_url)
    rule_kind = url_rule.get("kind", "path_tail")
    if rule_kind == "query":
        sample_value = (parse_qs(parsed.query).get(str(url_rule.get("key")), [""])[0]).strip()
    elif rule_kind == "fixed":
        sample_value = "shared-link"
    else:
        sample_value = next((part for part in reversed(parsed.path.split("/")) if part), "")
    explicit_type = str(control_facts.get("code_type", "")).lower()
    code_type = "shared" if rule_kind == "fixed" or explicit_type in {"fixed", "shared", "batch"} else "unique"

    domain_control = bool(control_facts.get("domain_control"))
    old_system_control = bool(control_facts.get("old_system_control"))
    product_mapping = bool(control_facts.get("product_mapping", control_facts.get("product_mapping_complete", False)))
    batch_mapping = bool(control_facts.get("batch_mapping", control_facts.get("batch_mapping_complete", False)))
    channel_mapping = bool(control_facts.get("channel_mapping", control_facts.get("channel_mapping_complete", False)))

    if mode == TakeoverMode.cname and domain_control and consumer_domain:
        recommendation = "cname"
        recommendation_reason = "消费者扫码域名可控，适合先核验 CNAME 与 HTTPS 后接入域名网关"
    elif mode == TakeoverMode.legacy_redirect and old_system_control and source_domain:
        recommendation = "legacy_redirect"
        recommendation_reason = "旧系统可配合修改，适合按样本码或码段执行外部跳转灰度"
    elif domain_control or old_system_control:
        recommendation = "collect_more_information"
        recommendation_reason = "已有部分控制条件，但当前模式缺少完成接管所需的外部事实"
    else:
        recommendation = "unsupported"
        recommendation_reason = "域名和旧系统均不可控，建议重新贴码或改造旧系统后再接管"

    unique_full = code_type == "unique"
    shared_degraded = code_type == "shared"
    capabilities = {
        "marketing": {
            "level": "full" if product_mapping or shared_degraded else "degraded",
            "reason": "可关联产品营销页面" if product_mapping else "需要补充产品映射，只能提供基础兜底页面",
        },
        "traceability": {
            "level": "full" if batch_mapping else ("degraded" if shared_degraded else "unsupported"),
            "reason": "已具备生产批次映射" if batch_mapping else "缺少批次主数据，固定链接最多提供批次级内容",
        },
        "light_verification": {
            "level": "full" if unique_full and product_mapping else ("degraded" if shared_degraded else "unsupported"),
            "reason": "逐码身份可追踪" if unique_full and product_mapping else "固定链接不能提供逐码验真结论",
        },
        "diversion": {
            "level": "full" if unique_full and channel_mapping else ("degraded" if unique_full else "unsupported"),
            "reason": "具备逐码渠道和区域映射"
            if unique_full and channel_mapping
            else "只能生成可调查线索，不能自动定性或处罚",
        },
        "analytics": {
            "level": "full" if unique_full else "degraded",
            "reason": "保留逐码扫码事实" if unique_full else "固定链接只保留共享入口级统计",
        },
    }
    risks = []
    if code_type == "shared":
        risks.append("这是固定链接或共享码，不开放逐码验真、逐码归因和逐码防窜")
    fallback_hosts = {_normalize_domain(consumer_domain)}
    if mode == TakeoverMode.cname:
        fallback_hosts.add(_normalize_domain(source_domain))
    if not fallback_url or _host(fallback_url) in fallback_hosts:
        risks.append("回退目标必须是独立可达地址，不能指回已经接入的消费者域名")
    if mode == TakeoverMode.cname and not domain_control:
        risks.append("尚未证明客户拥有消费者扫码域名控制权")
    return {
        "version": 1,
        "code_type": code_type,
        "sample_code": normalize_legacy_code(sample_value) if sample_value else None,
        "recommended_mode": recommendation,
        "recommendation_reason": recommendation_reason,
        "capabilities": capabilities,
        "risks": risks,
        "next_steps": [
            "完成旧码导入预检并修复失败项",
            "用真实旧链接完成样本预览和探测",
            "配置独立回退目标并由责任人确认",
        ],
    }


def serialize_project(project: TakeoverProject) -> dict:
    return {
        "id": project.id,
        "tenant_id": project.tenant_id,
        "name": project.name,
        "source_system": project.source_system,
        "mode": project.mode.value if hasattr(project.mode, "value") else project.mode,
        "source_domain": project.source_domain,
        "consumer_domain": project.consumer_domain,
        "expected_cname": project.expected_cname,
        "sample_url": project.sample_url,
        "url_rule": project.url_rule,
        "code_scope": project.code_scope,
        "control_facts": project.control_facts,
        "responsible_person": project.responsible_person,
        "technical_owner": project.technical_owner,
        "rollback_contact": project.rollback_contact,
        "fallback_url": project.fallback_url,
        "status": project.status.value if hasattr(project.status, "value") else project.status,
        "assessment": project.assessment,
        "readiness_snapshot": project.readiness_snapshot,
        "readiness_digest": project.readiness_digest,
        "configuration_version": project.configuration_version,
        "brand_confirmed_by": project.brand_confirmed_by,
        "brand_confirmed_at": project.brand_confirmed_at,
        "brand_confirmation_digest": project.brand_confirmation_digest,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


async def get_project(db: AsyncSession, tenant_id: uuid.UUID, project_id: uuid.UUID) -> TakeoverProject | None:
    return await db.scalar(
        select(TakeoverProject).where(TakeoverProject.id == project_id, TakeoverProject.tenant_id == tenant_id)
    )


async def create_project(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    data: dict,
) -> TakeoverProject:
    sample_url = _safe_url(str(data["sample_url"]))
    fallback_url = _safe_url(str(data["fallback_url"]))
    source_domain = _normalize_domain(data.get("source_domain"))
    consumer_domain = _normalize_domain(data.get("consumer_domain"))
    control_facts = dict(data.get("control_facts") or {})
    project = TakeoverProject(
        tenant_id=tenant_id,
        name=data["name"],
        source_system=data["source_system"],
        mode=data["mode"],
        source_domain=source_domain,
        consumer_domain=consumer_domain,
        expected_cname=data.get("expected_cname", DEFAULT_CNAME_TARGET),
        sample_url=sample_url,
        url_rule=data.get("url_rule") or {"kind": "path_tail"},
        code_scope=data.get("code_scope") or {},
        control_facts=control_facts,
        responsible_person=data["responsible_person"],
        technical_owner=data.get("technical_owner"),
        rollback_contact=data["rollback_contact"],
        fallback_url=fallback_url,
        status=TakeoverProjectStatus.draft,
        assessment=assess_project_inputs(
            mode=data["mode"],
            sample_url=sample_url,
            url_rule=data.get("url_rule") or {"kind": "path_tail"},
            control_facts=control_facts,
            source_domain=source_domain,
            consumer_domain=consumer_domain,
            fallback_url=fallback_url,
        ),
        readiness_snapshot={},
        created_by=account_id,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    await write_audit_log(
        db,
        str(account_id),
        str(tenant_id),
        "takeover_project_created",
        f"takeover_project:{project.id}",
        {"mode": data["mode"], "code_type": project.assessment["code_type"]},
    )
    return project


async def update_project(
    db: AsyncSession,
    project: TakeoverProject,
    account_id: uuid.UUID,
    data: dict,
) -> TakeoverProject:
    for key, value in data.items():
        if value is not None and hasattr(project, key):
            setattr(project, key, str(value) if key == "fallback_url" else value)
    project.configuration_version += 1
    project.brand_confirmed_by = None
    project.brand_confirmed_at = None
    project.brand_confirmation_digest = None
    project.status = TakeoverProjectStatus.needs_fix
    project.assessment = assess_project_inputs(
        mode=str(project.mode),
        sample_url=project.sample_url,
        url_rule=project.url_rule,
        control_facts=project.control_facts,
        source_domain=project.source_domain,
        consumer_domain=project.consumer_domain,
        fallback_url=project.fallback_url,
    )
    await db.flush()
    await db.refresh(project)
    await write_audit_log(
        db,
        str(account_id),
        str(project.tenant_id),
        "takeover_project_updated",
        f"takeover_project:{project.id}",
        {"configuration_version": project.configuration_version},
    )
    return project


def extract_legacy_code(project: TakeoverProject, raw_url: str) -> str:
    _safe_url(raw_url)
    parsed = urlparse(raw_url)
    allowed_hosts = {_normalize_domain(project.source_domain), _normalize_domain(project.consumer_domain)} - {None}
    if allowed_hosts and _normalize_domain(parsed.hostname) not in allowed_hosts:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="旧链接域名不属于当前接管项目")
    kind = project.url_rule.get("kind", "path_tail")
    if kind == "fixed":
        return "SHARED-LINK"
    if kind == "query":
        values = parse_qs(parsed.query).get(str(project.url_rule.get("key")), [])
        if len(values) != 1 or not values[0].strip():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="旧链接缺少受支持的取码参数")
        value = values[0]
    else:
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="旧链接缺少路径码")
        value = parts[-1]
    prefix = project.url_rule.get("prefix")
    if prefix and not value.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="旧链接不符合项目取码规则")
    return normalize_legacy_code(value)


def serialize_alias(alias: TakeoverAlias) -> dict:
    status_value = alias.status.value if hasattr(alias.status, "value") else alias.status
    type_value = alias.alias_type.value if hasattr(alias.alias_type, "value") else alias.alias_type
    return {
        "id": alias.id,
        "legacy_code": alias.legacy_code,
        "normalized_code": alias.normalized_code,
        "alias_type": type_value,
        "status": status_value,
        "target": {
            "public_id": getattr(alias, "_target_public_id", None),
            "product_id": alias.product_id,
            "production_batch_id": alias.production_batch_id,
        },
        "capabilities": alias.capabilities,
        "disabled_reason": alias.disabled_reason,
    }


async def _attach_alias_target(db: AsyncSession, alias: TakeoverAlias) -> TakeoverAlias:
    if alias.internal_code_id:
        code = await db.scalar(
            select(CodeItem).where(CodeItem.id == alias.internal_code_id, CodeItem.tenant_id == alias.tenant_id)
        )
        alias._target_public_id = code.public_id if code else None
    else:
        alias._target_public_id = None
    return alias


async def resolve_alias(
    db: AsyncSession,
    project: TakeoverProject,
    raw_url: str,
    *,
    include_staged: bool = False,
) -> TakeoverAlias | None:
    code = extract_legacy_code(project, raw_url)
    allowed_statuses = [TakeoverAliasStatus.active]
    if include_staged:
        allowed_statuses.append(TakeoverAliasStatus.staged)
    alias = await db.scalar(
        select(TakeoverAlias).where(
            TakeoverAlias.tenant_id == project.tenant_id,
            TakeoverAlias.project_id == project.id,
            TakeoverAlias.normalized_code == code,
            TakeoverAlias.status.in_(allowed_statuses),
        )
    )
    if alias:
        await _attach_alias_target(db, alias)
    return alias


async def preview_alias(db: AsyncSession, project: TakeoverProject, raw_url: str) -> dict:
    alias = await resolve_alias(db, project, raw_url, include_staged=True)
    if not alias:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="该旧链接尚未建立可用旧码映射")
    return {
        "project_id": project.id,
        "source_url": raw_url,
        "legacy_code": alias.legacy_code,
        "status": "resolved",
        "target": {
            "public_id": getattr(alias, "_target_public_id", None),
            "product_id": alias.product_id,
            "production_batch_id": alias.production_batch_id,
            "status": alias.status.value if hasattr(alias.status, "value") else alias.status,
            "alias_status": alias.status.value if hasattr(alias.status, "value") else alias.status,
        },
        "alias_type": alias.alias_type.value if hasattr(alias.alias_type, "value") else alias.alias_type,
        "capabilities": alias.capabilities,
        "degradation_notice": (
            "固定链接只支持共享商品、批次或营销内容，不提供逐码验真、逐码归因和逐码防窜"
            if alias.alias_type == TakeoverAliasType.shared
            else None
        ),
    }


async def _find_code_mapping(
    db: AsyncSession, tenant_id: uuid.UUID, row: dict
) -> tuple[dict | None, tuple[str, str, bool] | None]:
    legacy_code = str(row.get("legacy_code", "")).strip()
    if not legacy_code:
        return None, ("missing_legacy_code", "legacy_code 不能为空", True)
    internal_public_id = str(row.get("internal_public_id", "")).strip()
    if not internal_public_id:
        return None, ("missing_internal_code", "必须提供一码通内部业务码 internal_public_id", True)
    code = await db.scalar(
        select(CodeItem).where(CodeItem.tenant_id == tenant_id, CodeItem.public_id == internal_public_id)
    )
    if not code:
        return None, ("internal_code_not_found", "内部业务码不存在或不属于当前租户", True)
    batch = await db.scalar(
        select(CodeBatch).where(CodeBatch.tenant_id == tenant_id, CodeBatch.id == code.code_batch_id)
    )
    if not batch:
        return None, ("code_batch_not_found", "内部业务码所属批次不存在", True)
    if row.get("sku_code"):
        sku = await db.scalar(select(SKU).where(SKU.tenant_id == tenant_id, SKU.id == batch.sku_id))
        if not sku or sku.code != str(row["sku_code"]).strip():
            return None, ("sku_mismatch", "SKU 编码与内部码所属批次不一致", True)
    if row.get("batch_code"):
        production_batch = await db.scalar(
            select(ProductionBatch).where(
                ProductionBatch.tenant_id == tenant_id,
                ProductionBatch.id == batch.production_batch_id,
            )
        )
        if not production_batch or production_batch.batch_code != str(row["batch_code"]).strip():
            return None, ("batch_mismatch", "生产批次号与内部码所属批次不一致", True)
    alias_type = (
        TakeoverAliasType.shared
        if str(row.get("code_type", "")).lower() in {"fixed", "shared", "batch"}
        else TakeoverAliasType.unique
    )
    return {
        **row,
        "legacy_code": legacy_code,
        "_normalized_code": normalize_legacy_code(legacy_code),
        "_alias_type": alias_type.value,
        "_internal_code_id": str(code.id),
        "_product_id": str(batch.product_id),
        "_production_batch_id": str(batch.production_batch_id) if batch.production_batch_id else None,
        "_capabilities": {
            "marketing": "full",
            "traceability": "degraded" if alias_type == TakeoverAliasType.shared else "full",
            "light_verification": "degraded" if alias_type == TakeoverAliasType.shared else "full",
            "diversion": "unsupported" if alias_type == TakeoverAliasType.shared else "degraded",
        },
    }, None


async def _parse_import_rows(db: AsyncSession, tenant_id: uuid.UUID, content: bytes) -> tuple[list[dict], list[dict]]:
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=400, detail="导入文件不能超过 10MB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV 必须使用 UTF-8 编码") from exc
    reader = csv.DictReader(io.StringIO(text))
    fields = set(reader.fieldnames or [])
    missing = {"legacy_code", "internal_public_id"} - fields
    unknown = fields - SUPPORTED_IMPORT_COLUMNS
    if missing:
        raise HTTPException(status_code=400, detail=f"导入模板缺少字段：{', '.join(sorted(missing))}")
    if unknown:
        raise HTTPException(status_code=400, detail=f"导入模板包含不支持字段：{', '.join(sorted(unknown))}")
    rows: list[dict] = []
    errors: list[dict] = []
    seen: set[str] = set()
    for row_number, source_row in enumerate(reader, start=2):
        if row_number - 1 > MAX_IMPORT_ROWS:
            raise HTTPException(status_code=400, detail="单个导入文件最多 100000 行")
        row = {key: (value or "").strip() for key, value in source_row.items() if key}
        normalized = normalize_legacy_code(row.get("legacy_code", ""))
        if normalized in seen and normalized:
            errors.append(
                {
                    "row_number": row_number,
                    "legacy_code": row.get("legacy_code"),
                    "error_code": "duplicate",
                    "message": "同一文件中重复出现旧编号",
                    "retryable": False,
                    "raw_row": row,
                }
            )
            continue
        seen.add(normalized)
        mapped, error = await _find_code_mapping(db, tenant_id, row)
        if error:
            code, message, retryable = error
            errors.append(
                {
                    "row_number": row_number,
                    "legacy_code": row.get("legacy_code"),
                    "error_code": code,
                    "message": message,
                    "retryable": retryable,
                    "raw_row": row,
                }
            )
        else:
            mapped["_row_number"] = row_number
            rows.append(mapped)
    return rows, errors


def serialize_import(job: TakeoverImportJob, errors: list[TakeoverImportError] | list[dict]) -> dict:
    serialized_errors = []
    for error in errors:
        serialized_errors.append(
            {
                "row_number": error.row_number if hasattr(error, "row_number") else error["row_number"],
                "legacy_code": error.legacy_code if hasattr(error, "legacy_code") else error.get("legacy_code"),
                "error_code": error.error_code if hasattr(error, "error_code") else error["error_code"],
                "message": error.message if hasattr(error, "message") else error["message"],
                "retryable": error.retryable if hasattr(error, "retryable") else error["retryable"],
            }
        )
    return {
        "id": job.id,
        "project_id": job.project_id,
        "status": job.status.value if hasattr(job.status, "value") else job.status,
        "file_name": job.file_name,
        "file_sha256": job.file_sha256,
        "counts": job.counts,
        "error_detail": job.error_detail,
        "errors": serialized_errors,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }


async def dry_run_import(
    db: AsyncSession,
    project: TakeoverProject,
    account_id: uuid.UUID,
    file_name: str,
    content: bytes,
) -> TakeoverImportJob:
    file_sha256 = hashlib.sha256(content).hexdigest()
    existing = await db.scalar(
        select(TakeoverImportJob).where(
            TakeoverImportJob.project_id == project.id,
            TakeoverImportJob.file_sha256 == file_sha256,
        )
    )
    if existing:
        return existing
    valid_rows, errors = await _parse_import_rows(db, project.tenant_id, content)
    counts = {
        "total": len(valid_rows) + len(errors),
        "valid": len(valid_rows),
        "failed": len(errors),
        "duplicate": sum(1 for error in errors if error["error_code"] == "duplicate"),
        "conflict": 0,
        "succeeded": 0,
    }
    job = TakeoverImportJob(
        tenant_id=project.tenant_id,
        project_id=project.id,
        file_name=file_name,
        file_sha256=file_sha256,
        status=TakeoverImportStatus.dry_run,
        source_rows=valid_rows,
        counts=counts,
        created_by=account_id,
    )
    db.add(job)
    await db.flush()
    for error in errors:
        db.add(
            TakeoverImportError(
                tenant_id=project.tenant_id,
                job_id=job.id,
                row_number=error["row_number"],
                legacy_code=error.get("legacy_code"),
                error_code=error["error_code"],
                message=error["message"],
                retryable=error["retryable"],
                raw_row=error["raw_row"],
            )
        )
    await db.flush()
    await write_audit_log(
        db,
        str(account_id),
        str(project.tenant_id),
        "takeover_import_dry_run",
        f"takeover_import:{job.id}",
        {"project_id": str(project.id), "counts": counts},
    )
    return job


async def _job_errors(db: AsyncSession, job_id: uuid.UUID) -> list[TakeoverImportError]:
    return list(
        (
            await db.scalars(
                select(TakeoverImportError)
                .where(TakeoverImportError.job_id == job_id)
                .order_by(TakeoverImportError.row_number)
            )
        ).all()
    )


async def queue_import(
    db: AsyncSession, project: TakeoverProject, job: TakeoverImportJob, account_id: uuid.UUID
) -> TakeoverImportJob:
    """确认导入并交给持久化 worker；SQLite 测试环境没有 worker 时直接执行。"""
    if job.project_id != project.id or job.tenant_id != project.tenant_id:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    if job.status in {TakeoverImportStatus.completed, TakeoverImportStatus.partial_failed}:
        return job
    retrying_after_renewal = (
        job.status == TakeoverImportStatus.failed and job.error_detail == TAKEOVER_IMPORT_PLAN_EXPIRED_ERROR
    )
    if job.status not in {
        TakeoverImportStatus.dry_run,
        TakeoverImportStatus.pending,
        TakeoverImportStatus.processing,
    } and not (retrying_after_renewal):
        raise HTTPException(status_code=409, detail="当前导入任务不能提交")
    if job.status == TakeoverImportStatus.dry_run and job.counts.get("failed", 0):
        raise HTTPException(status_code=409, detail="Dry-run 仍有失败项，请修复后重试")

    job.status = TakeoverImportStatus.pending
    job.error_detail = None
    job.submitted_at = _now() if retrying_after_renewal else job.submitted_at or _now()
    bind = db.get_bind()
    if bind.dialect.name == "sqlite":
        return await submit_import(db, project, job, account_id)

    await db.flush()
    try:
        import redis.asyncio as aioredis

        from app.core.config import settings

        async with aioredis.from_url(settings.redis_url) as redis:
            await redis.lpush(TAKEOVER_IMPORT_QUEUE_KEY, str(job.id))
    except Exception as exc:
        job.status = TakeoverImportStatus.failed
        job.error_detail = f"后台导入队列不可用：{exc}"
        await db.flush()
        raise HTTPException(status_code=503, detail="后台导入队列暂不可用，请稍后重试") from exc
    await write_audit_log(
        db,
        str(account_id),
        str(project.tenant_id),
        "takeover_import_queued",
        f"takeover_import:{job.id}",
        {"project_id": str(project.id)},
    )
    return job


async def submit_import(
    db: AsyncSession,
    project: TakeoverProject,
    job: TakeoverImportJob,
    account_id: uuid.UUID,
    *,
    write_audit: bool = True,
) -> TakeoverImportJob:
    if job.project_id != project.id or job.tenant_id != project.tenant_id:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    if job.status in {TakeoverImportStatus.completed, TakeoverImportStatus.partial_failed}:
        return job
    if job.status not in {TakeoverImportStatus.dry_run, TakeoverImportStatus.pending, TakeoverImportStatus.processing}:
        raise HTTPException(status_code=409, detail="当前导入任务不能提交")
    job.status = TakeoverImportStatus.processing
    job.submitted_at = _now()
    errors = await _job_errors(db, job.id)
    failed = len(errors)
    succeeded = 0
    for row in job.source_rows:
        normalized = row["_normalized_code"]
        existing = await db.scalar(
            select(TakeoverAlias).where(
                TakeoverAlias.project_id == project.id,
                TakeoverAlias.normalized_code == normalized,
            )
        )
        target_id = uuid.UUID(row["_internal_code_id"])
        if existing:
            if existing.internal_code_id != target_id:
                db.add(
                    TakeoverImportError(
                        tenant_id=project.tenant_id,
                        job_id=job.id,
                        row_number=row["_row_number"],
                        legacy_code=row["legacy_code"],
                        error_code="conflict",
                        message="同一旧编号已有不同目标映射，必须人工选择后重试",
                        retryable=False,
                        raw_row=row,
                    )
                )
                failed += 1
            else:
                succeeded += 1
            continue
        db.add(
            TakeoverAlias(
                tenant_id=project.tenant_id,
                project_id=project.id,
                source_job_id=job.id,
                legacy_code=row["legacy_code"],
                normalized_code=normalized,
                alias_type=row["_alias_type"],
                internal_code_id=target_id,
                product_id=uuid.UUID(row["_product_id"]) if row.get("_product_id") else None,
                production_batch_id=uuid.UUID(row["_production_batch_id"]) if row.get("_production_batch_id") else None,
                status=TakeoverAliasStatus.staged,
                capabilities=row["_capabilities"],
            )
        )
        succeeded += 1
    job.counts = {**job.counts, "succeeded": succeeded, "failed": failed, "valid": succeeded}
    job.status = TakeoverImportStatus.completed if failed == 0 else TakeoverImportStatus.partial_failed
    job.completed_at = _now()
    project.status = TakeoverProjectStatus.needs_fix if failed else TakeoverProjectStatus.draft
    await db.flush()
    if write_audit:
        await write_audit_log(
            db,
            str(account_id),
            str(project.tenant_id),
            "takeover_import_submitted",
            f"takeover_import:{job.id}",
            {"project_id": str(project.id), "counts": job.counts},
        )
    return job


async def process_import_job(job_id: uuid.UUID | str) -> None:
    """Worker 入口：领取一个待处理任务，并将最终状态持久化。"""
    from app.core.database import (
        async_session_factory,
        bootstrap_tenant_keys,
        control_session_factory,
        lock_active_tenant_context,
        set_session_tenant_context,
    )

    parsed_job_id = uuid.UUID(str(job_id))
    async with async_session_factory() as bootstrap_db:
        work_keys = await bootstrap_tenant_keys(
            bootstrap_db,
            select(TakeoverImportJob.id, TakeoverImportJob.tenant_id)
            .where(TakeoverImportJob.id == parsed_job_id)
            .limit(1),
        )
    if not work_keys:
        return
    _, tenant_id = work_keys[0]

    async with async_session_factory() as db:
        try:
            # The tenant lock linearizes the complete import transaction with a
            # platform plan-expiry update.  It must be acquired before reading
            # the job, project, errors, or aliases and held through commit.
            await lock_active_tenant_context(db, tenant_id)
        except TenantPlanExpiredError:
            # Expiry is a control-plane lifecycle outcome, not a tenant
            # business write.  Persist it through the trusted bootstrap pair so
            # the job has a stable, recoverable state before releasing the
            # tenant lock to a concurrent renewal.  The exact bootstrap pair
            # prevents opening RLS to a caller-supplied tenant id.
            try:
                async with control_session_factory() as status_db:
                    if status_db.get_bind().dialect.name == "postgresql":
                        await status_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                    await status_db.execute(
                        update(TakeoverImportJob)
                        .where(
                            TakeoverImportJob.id == parsed_job_id,
                            TakeoverImportJob.tenant_id == tenant_id,
                            TakeoverImportJob.status.in_({TakeoverImportStatus.dry_run, TakeoverImportStatus.pending}),
                        )
                        .values(
                            status=TakeoverImportStatus.failed,
                            error_detail=TAKEOVER_IMPORT_PLAN_EXPIRED_ERROR,
                            completed_at=None,
                        )
                    )
                    await status_db.commit()
            finally:
                await db.rollback()
            return
        job = await db.scalar(
            select(TakeoverImportJob)
            .where(TakeoverImportJob.id == parsed_job_id, TakeoverImportJob.tenant_id == tenant_id)
            .with_for_update()
        )
        if not job or job.status not in {TakeoverImportStatus.dry_run, TakeoverImportStatus.pending}:
            return
        project = await db.scalar(
            select(TakeoverProject).where(
                TakeoverProject.id == job.project_id,
                TakeoverProject.tenant_id == job.tenant_id,
            )
        )
        if not project:
            job.status = TakeoverImportStatus.failed
            job.error_detail = "接管项目不存在"
            await db.commit()
            return
        job.status = TakeoverImportStatus.processing
        await db.flush()
        try:
            await submit_import(db, project, job, job.created_by, write_audit=False)
            audit_payload = {
                "operator_id": str(job.created_by),
                "tenant_id": tenant_id,
                "resource": f"takeover_import:{job.id}",
                "details": {"project_id": str(project.id), "counts": job.counts},
            }
            await db.commit()
            try:
                async with control_session_factory() as audit_db:
                    await audit_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                    await write_audit_log(
                        audit_db,
                        audit_payload["operator_id"],
                        str(audit_payload["tenant_id"]),
                        "takeover_import_submitted",
                        audit_payload["resource"],
                        audit_payload["details"],
                    )
                    await audit_db.commit()
            except Exception:
                # The business transaction is already durable.  Do not turn a
                # successful import into a retryable failed job (which could
                # duplicate aliases); surface the independent audit failure.
                logger.exception("Failed to persist takeover import audit for job %s", parsed_job_id)
        except Exception:
            await db.rollback()
            async with async_session_factory() as failed_db:
                await set_session_tenant_context(failed_db, tenant_id)
                failed_job = await failed_db.scalar(
                    select(TakeoverImportJob).where(
                        TakeoverImportJob.id == parsed_job_id,
                        TakeoverImportJob.tenant_id == tenant_id,
                    )
                )
                if failed_job:
                    failed_job.status = TakeoverImportStatus.failed
                    failed_job.error_detail = "后台导入任务执行失败，请查看逐行错误后重试"
                    await failed_db.commit()
            raise


async def retry_failed_import(
    db: AsyncSession,
    project: TakeoverProject,
    parent_job: TakeoverImportJob,
    account_id: uuid.UUID,
) -> TakeoverImportJob:
    errors = await _job_errors(db, parent_job.id)
    if not errors:
        raise HTTPException(status_code=409, detail="当前任务没有可重试失败项")
    rows = [error.raw_row for error in errors if error.retryable]
    if not rows:
        raise HTTPException(status_code=409, detail="当前失败项均不可重试")
    payload = "\ufeff" + ",".join(sorted(rows[0].keys())) + "\n"
    keys = sorted(rows[0].keys())
    payload += "\n".join(",".join(str(row.get(key, "")).replace(",", "，") for key in keys) for row in rows)
    retry_job = await dry_run_import(db, project, account_id, f"retry-{parent_job.file_name}", payload.encode())
    retry_job.parent_job_id = parent_job.id
    await db.flush()
    return retry_job


async def _latest_domain_check(db: AsyncSession, project: TakeoverProject) -> TakeoverDomainCheck | None:
    return await db.scalar(
        select(TakeoverDomainCheck)
        .where(TakeoverDomainCheck.project_id == project.id, TakeoverDomainCheck.tenant_id == project.tenant_id)
        .order_by(TakeoverDomainCheck.checked_at.desc())
        .limit(1)
    )


async def build_takeover_readiness(db: AsyncSession, project: TakeoverProject) -> tuple[dict, str]:
    latest_import = await db.scalar(
        select(TakeoverImportJob)
        .where(
            TakeoverImportJob.project_id == project.id,
            TakeoverImportJob.tenant_id == project.tenant_id,
            TakeoverImportJob.status == TakeoverImportStatus.completed,
        )
        .order_by(TakeoverImportJob.completed_at.desc())
        .limit(1)
    )
    alias_count = int(
        await db.scalar(
            select(func.count(TakeoverAlias.id)).where(
                TakeoverAlias.project_id == project.id,
                TakeoverAlias.tenant_id == project.tenant_id,
                TakeoverAlias.status != TakeoverAliasStatus.disabled,
            )
        )
        or 0
    )
    domain_check = await _latest_domain_check(db, project)
    latest_observation = await db.scalar(
        select(TakeoverObservation)
        .where(TakeoverObservation.project_id == project.id, TakeoverObservation.tenant_id == project.tenant_id)
        .order_by(TakeoverObservation.created_at.desc())
        .limit(1)
    )
    is_cname = str(project.mode) == TakeoverMode.cname
    fallback_hosts = {_normalize_domain(project.consumer_domain)}
    if is_cname:
        fallback_hosts.add(_normalize_domain(project.source_domain))
    fallback_ok = bool(project.fallback_url and _host(project.fallback_url) not in fallback_hosts)
    sample_alias = None
    try:
        sample_alias = await resolve_alias(db, project, project.sample_url, include_staged=True)
    except HTTPException:
        sample_alias = None
    checks = [
        _check(
            "project_assessed",
            "接管事实评估已完成",
            bool(project.assessment),
            "已保存编码类型、接管模式和能力矩阵" if project.assessment else "请先提交接管评估",
        ),
        _check(
            "import_coverage",
            "旧码导入已完成且无失败",
            bool(latest_import and latest_import.counts.get("failed", 0) == 0),
            "正式导入任务已完成" if latest_import else "请先完成 Dry-run 并提交正式导入",
        ),
        _check(
            "aliases_ready",
            "旧码别名已建立",
            alias_count > 0,
            f"当前已建立 {alias_count} 条可回查别名" if alias_count else "请先完成正式导入",
        ),
        _check(
            "sample_resolution",
            "真实旧链接可以解析",
            bool(sample_alias),
            "样本旧链接已解析到业务对象" if sample_alias else "请检查 URL 取码规则和别名映射",
        ),
        _check(
            "fallback_target",
            "独立回退目标已配置",
            fallback_ok,
            "回退目标不指向接管域名" if fallback_ok else "回退目标必须是独立可达地址",
        ),
        _check(
            "rollback_contact",
            "回退责任人已指定",
            bool(project.rollback_contact.strip()),
            "已指定回退联系人" if project.rollback_contact else "请指定回退联系人",
        ),
        _check(
            "consumer_domain_verified",
            "消费者扫码域名已完成 DNS/CNAME 与 HTTPS 核验",
            not is_cname
            or bool(domain_check and domain_check.status == "passed" and domain_check.tls_status == "active"),
            "已通过真实 DNS、CNAME 和 HTTPS 核验"
            if not is_cname
            or (domain_check and domain_check.status == "passed" and domain_check.tls_status == "active")
            else "请完成公网 DNS/CNAME 与 HTTPS 核验",
        ),
        _check(
            "monitoring_available",
            "观察指标可用",
            bool(latest_observation or project.control_facts.get("monitoring_ready")),
            "已有真实探测或监控配置"
            if latest_observation or project.control_facts.get("monitoring_ready")
            else "请先完成样本探测并配置观察指标",
        ),
    ]
    snapshot = {
        "version": 1,
        "project_id": str(project.id),
        "configuration_version": project.configuration_version,
        "checks": checks,
        "passed_count": sum(1 for item in checks if item["passed"]),
        "total_count": len(checks),
        "ready": all(item["passed"] for item in checks if item["blocking"]),
        "calculated_at": _now().isoformat(),
    }
    digest = _digest({key: value for key, value in snapshot.items() if key != "calculated_at"})
    return snapshot, digest


async def refresh_takeover_readiness(db: AsyncSession, project: TakeoverProject) -> TakeoverProject:
    snapshot, digest = await build_takeover_readiness(db, project)
    previous_digest = project.readiness_digest
    project.readiness_snapshot = snapshot
    project.readiness_digest = digest
    if project.brand_confirmation_digest and project.brand_confirmation_digest != digest:
        project.brand_confirmed_by = None
        project.brand_confirmed_at = None
        project.brand_confirmation_digest = None
        project.status = TakeoverProjectStatus.needs_fix
    elif project.status in {TakeoverProjectStatus.draft, TakeoverProjectStatus.needs_fix}:
        project.status = (
            TakeoverProjectStatus.ready_for_confirmation if snapshot["ready"] else TakeoverProjectStatus.needs_fix
        )
    if previous_digest and previous_digest != digest and project.status == TakeoverProjectStatus.ready_for_confirmation:
        project.status = TakeoverProjectStatus.needs_fix
    await db.flush()
    await db.refresh(project)
    return project


async def confirm_project(db: AsyncSession, project: TakeoverProject, account_id: uuid.UUID) -> TakeoverProject:
    await refresh_takeover_readiness(db, project)
    if not project.readiness_snapshot.get("ready"):
        raise HTTPException(status_code=409, detail="接管准备度未通过，暂时不能确认")
    project.brand_confirmed_by = account_id
    project.brand_confirmed_at = _now()
    project.brand_confirmation_digest = project.readiness_digest
    project.status = TakeoverProjectStatus.cutover_ready
    await db.flush()
    await db.refresh(project)
    await write_audit_log(
        db,
        str(account_id),
        str(project.tenant_id),
        "takeover_brand_confirmed",
        f"takeover_project:{project.id}",
        {"readiness_digest": project.readiness_digest},
    )
    return project


def _parse_certificate_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    except ValueError:
        try:
            return parsedate_to_datetime(value).astimezone(UTC)
        except (TypeError, ValueError, OverflowError):
            return None


def _inspect_dns_and_tls_sync(domain: str, expected_cname: str) -> dict:
    from app.core.config import settings

    observed_cnames: list[str] = []
    observed_ips: list[str] = []
    ttl: int | None = None
    dns_error = None
    resolver = None
    try:
        import dns.resolver

        resolver = dns.resolver.Resolver()
        if settings.takeover_dns_nameserver:
            resolver.nameservers = [settings.takeover_dns_nameserver]
            resolver.nameserver_ports = {settings.takeover_dns_nameserver: settings.takeover_dns_port}
        answer = resolver.resolve(domain, "CNAME", lifetime=5)
        observed_cnames = [_normalize_domain(str(item.target)) or "" for item in answer]
        ttl = int(answer.rrset.ttl) if answer.rrset else None
    except Exception as exc:  # pragma: no cover - actual network is covered by deployment smoke
        dns_error = str(exc)
    try:
        if resolver:
            answer = resolver.resolve(domain, "A", lifetime=5)
            observed_ips = sorted({str(item) for item in answer})
        else:
            infos = socket.getaddrinfo(domain, settings.takeover_tls_port, type=socket.SOCK_STREAM)
            observed_ips = sorted({info[4][0] for info in infos})
    except Exception as exc:  # pragma: no cover - actual network is covered by deployment smoke
        if not dns_error:
            dns_error = str(exc)
    tls_status = "invalid"
    certificate_expires_at = None
    tls_error = None
    try:
        context = ssl.create_default_context(cafile=settings.takeover_tls_ca_file or None)
        tls_host = observed_ips[0] if observed_ips else domain
        with socket.create_connection((tls_host, settings.takeover_tls_port), timeout=5) as raw_socket:
            with context.wrap_socket(raw_socket, server_hostname=domain) as tls_socket:
                certificate = tls_socket.getpeercert()
                certificate_expires_at = _parse_certificate_expiry(certificate.get("notAfter"))
                tls_status = "active"
    except Exception as exc:  # pragma: no cover - actual network is covered by deployment smoke
        tls_error = str(exc)
    cname_passed = _normalize_domain(expected_cname) in set(observed_cnames)
    passed = cname_passed and tls_status == "active"
    return {
        "status": "passed" if passed else ("failed" if observed_cnames or observed_ips else "pending_external"),
        "observed_cnames": observed_cnames,
        "observed_ips": observed_ips,
        "ttl": ttl,
        "tls_status": tls_status,
        "certificate_expires_at": certificate_expires_at,
        "failure_reason": None
        if passed
        else "; ".join(
            item for item in [dns_error, tls_error, "CNAME 目标不匹配" if not cname_passed else None] if item
        ),
        "raw_observation": {"dns_error": dns_error, "tls_error": tls_error},
    }


async def inspect_domain(db: AsyncSession, project: TakeoverProject, account_id: uuid.UUID) -> TakeoverDomainCheck:
    if not project.consumer_domain:
        raise HTTPException(status_code=409, detail="项目尚未配置消费者扫码域名")
    observation = await asyncio.to_thread(_inspect_dns_and_tls_sync, project.consumer_domain, project.expected_cname)
    check = TakeoverDomainCheck(
        tenant_id=project.tenant_id,
        project_id=project.id,
        domain=project.consumer_domain,
        expected_cname=project.expected_cname,
        **observation,
    )
    db.add(check)
    await db.flush()
    await refresh_takeover_readiness(db, project)
    await write_audit_log(
        db,
        str(account_id),
        str(project.tenant_id),
        "takeover_domain_checked",
        f"takeover_project:{project.id}",
        {"domain": project.consumer_domain, "status": check.status, "tls_status": check.tls_status},
    )
    return check


def serialize_domain_check(check: TakeoverDomainCheck) -> dict:
    return {
        "id": check.id,
        "domain": check.domain,
        "expected_cname": check.expected_cname,
        "observed_cnames": check.observed_cnames,
        "observed_ips": check.observed_ips,
        "ttl": check.ttl,
        "tls_status": check.tls_status,
        "certificate_expires_at": check.certificate_expires_at,
        "status": check.status,
        "failure_reason": check.failure_reason,
        "checked_at": check.checked_at,
    }


def _route_payload(project: TakeoverProject, data: dict) -> dict:
    source_url = _safe_url(str(data["source_url"]))
    target_url = _safe_url(str(data["target_url"]))
    source_host = _host(source_url)
    fallback_host = _host(project.fallback_url)
    if str(project.mode) == TakeoverMode.cname and source_host == fallback_host:
        raise HTTPException(status_code=422, detail="旧入口和回退目标必须使用独立域名，避免回源循环")
    if project.consumer_domain and _host(target_url) == _normalize_domain(project.consumer_domain):
        raise HTTPException(status_code=422, detail="新链路目标不能再次指向接管域名")
    return {
        "mode": str(project.mode),
        "domain": project.consumer_domain or project.source_domain,
        "source_url": source_url,
        "target_url": target_url,
        "extraction_rule": data.get("extraction_rule") or project.url_rule,
        "sample_codes": [normalize_legacy_code(code) for code in data.get("sample_codes", [])],
        "code_prefix": data.get("code_prefix"),
    }


def serialize_route(route: TakeoverRouteVersion) -> dict:
    return {
        "id": route.id,
        "project_id": route.project_id,
        "version": route.version,
        "mode": route.mode.value if hasattr(route.mode, "value") else route.mode,
        "domain": route.domain,
        "source_url": route.source_url,
        "target_url": route.target_url,
        "extraction_rule": route.extraction_rule,
        "sample_codes": route.sample_codes,
        "code_prefix": route.code_prefix,
        "status": route.status.value if hasattr(route.status, "value") else route.status,
        "content_digest": route.content_digest,
        "readiness_snapshot": route.readiness_snapshot,
        "executed_at": route.executed_at,
    }


async def create_route(
    db: AsyncSession, project: TakeoverProject, account_id: uuid.UUID, data: dict
) -> TakeoverRouteVersion:
    last_version = await db.scalar(
        select(func.max(TakeoverRouteVersion.version)).where(TakeoverRouteVersion.project_id == project.id)
    )
    version = int(last_version or 0) + 1
    payload = _route_payload(project, data)
    readiness, _ = await build_takeover_readiness(db, project)
    route = TakeoverRouteVersion(
        tenant_id=project.tenant_id,
        project_id=project.id,
        version=version,
        **payload,
        status=TakeoverRouteStatus.candidate,
        content_digest=_digest(payload),
        readiness_snapshot=readiness,
        created_by=account_id,
    )
    db.add(route)
    await db.flush()
    await write_audit_log(
        db,
        str(account_id),
        str(project.tenant_id),
        "takeover_route_candidate_created",
        f"takeover_route:{route.id}",
        {"version": version},
    )
    return route


async def get_route(db: AsyncSession, project: TakeoverProject, route_id: uuid.UUID) -> TakeoverRouteVersion | None:
    return await db.scalar(
        select(TakeoverRouteVersion).where(
            TakeoverRouteVersion.id == route_id,
            TakeoverRouteVersion.project_id == project.id,
            TakeoverRouteVersion.tenant_id == project.tenant_id,
        )
    )


async def _event_exists(db: AsyncSession, project: TakeoverProject, key: str | None) -> TakeoverCutoverEvent | None:
    if not key:
        return None
    return await db.scalar(
        select(TakeoverCutoverEvent).where(
            TakeoverCutoverEvent.project_id == project.id,
            TakeoverCutoverEvent.idempotency_key == key,
        )
    )


async def _write_event(
    db: AsyncSession,
    project: TakeoverProject,
    route: TakeoverRouteVersion | None,
    account_id: uuid.UUID,
    action: str,
    state: str,
    *,
    key: str | None = None,
    details: dict | None = None,
) -> TakeoverCutoverEvent:
    event = TakeoverCutoverEvent(
        tenant_id=project.tenant_id,
        project_id=project.id,
        route_version_id=route.id if route else None,
        action=action,
        state=state,
        idempotency_key=key,
        actor_id=account_id,
        details=details or {},
    )
    db.add(event)
    await db.flush()
    return event


async def confirm_route(
    db: AsyncSession, project: TakeoverProject, route: TakeoverRouteVersion, account_id: uuid.UUID
) -> TakeoverRouteVersion:
    await refresh_takeover_readiness(db, project)
    if not project.readiness_snapshot.get("ready"):
        raise HTTPException(status_code=409, detail="路由候选版本的接管准备度未通过")
    if not project.brand_confirmation_digest:
        await confirm_project(db, project, account_id)
    route.status = TakeoverRouteStatus.confirmed
    route.brand_confirmation_digest = project.brand_confirmation_digest
    route.readiness_snapshot = project.readiness_snapshot
    await db.flush()
    return route


async def _activate_route_aliases(db: AsyncSession, project: TakeoverProject, route: TakeoverRouteVersion) -> None:
    aliases = list(
        (
            await db.scalars(
                select(TakeoverAlias).where(
                    TakeoverAlias.project_id == project.id,
                    TakeoverAlias.tenant_id == project.tenant_id,
                    TakeoverAlias.status == TakeoverAliasStatus.staged,
                )
            )
        ).all()
    )
    selected = set(route.sample_codes)
    for alias in aliases:
        if not selected and not route.code_prefix:
            should_activate = True
        else:
            should_activate = alias.normalized_code in selected or bool(
                route.code_prefix and alias.normalized_code.startswith(normalize_legacy_code(route.code_prefix))
            )
        if should_activate:
            alias.status = TakeoverAliasStatus.active


async def cutover_route(
    db: AsyncSession, project: TakeoverProject, route: TakeoverRouteVersion, account_id: uuid.UUID, idempotency_key: str
) -> TakeoverRouteVersion:
    existing = await _event_exists(db, project, idempotency_key)
    if existing and existing.route_version_id == route.id:
        return route
    if route.status == TakeoverRouteStatus.active:
        return route
    await refresh_takeover_readiness(db, project)
    if project.brand_confirmation_digest != project.readiness_digest:
        raise HTTPException(status_code=409, detail="准备度已变化，需要重新确认")
    if route.status != TakeoverRouteStatus.confirmed:
        raise HTTPException(status_code=409, detail="路由版本尚未由品牌管理员确认")
    active = await db.scalar(
        select(TakeoverRouteVersion).where(
            TakeoverRouteVersion.project_id == project.id,
            TakeoverRouteVersion.status.in_((TakeoverRouteStatus.active, TakeoverRouteStatus.paused)),
            TakeoverRouteVersion.id != route.id,
        )
    )
    if active:
        raise HTTPException(status_code=409, detail="同一项目已有进行中的切换或观察，请先完成或回退")
    if str(project.mode) == TakeoverMode.legacy_redirect:
        external = await db.scalar(
            select(TakeoverCutoverEvent).where(
                TakeoverCutoverEvent.route_version_id == route.id,
                TakeoverCutoverEvent.action == "external_execution",
                TakeoverCutoverEvent.state == "recorded",
            )
        )
        if not external:
            project.status = TakeoverProjectStatus.pending_external
            await db.flush()
            raise HTTPException(status_code=409, detail="等待旧系统负责人完成外部跳转并回报证据")
    route.status = TakeoverRouteStatus.active
    route.executed_by = account_id
    route.executed_at = _now()
    project.active_route_version_id = route.id
    project.status = TakeoverProjectStatus.observing
    await _activate_route_aliases(db, project, route)
    await _write_event(
        db, project, route, account_id, "cutover", "observing", key=idempotency_key, details={"mode": str(project.mode)}
    )
    await db.flush()
    return route


async def record_external_execution(
    db: AsyncSession, project: TakeoverProject, route: TakeoverRouteVersion, account_id: uuid.UUID, data: dict
) -> TakeoverCutoverEvent:
    if route.status not in {TakeoverRouteStatus.candidate, TakeoverRouteStatus.confirmed}:
        raise HTTPException(status_code=409, detail="当前路由版本不能上报外部执行")
    event = await _write_event(db, project, route, account_id, "external_execution", "recorded", details=data)
    project.status = TakeoverProjectStatus.pending_external
    await db.flush()
    return event


async def record_observation(
    db: AsyncSession, project: TakeoverProject, route: TakeoverRouteVersion, account_id: uuid.UUID, data: dict
) -> TakeoverObservation:
    if route.status != TakeoverRouteStatus.active:
        raise HTTPException(status_code=409, detail="只有已进入观察中的路由可以上报指标")
    success_rate = float(data["success_rate"])
    error_rate = float(data["error_rate"])
    target_match = bool(data.get("target_match"))
    if success_rate >= 0.99 and error_rate <= 0.01 and target_match and float(data.get("h5_reach_rate", 0)) >= 0.99:
        recommendation = "continue"
        observation_status = "passed"
    elif success_rate < 0.9 or error_rate > 0.05:
        recommendation = "rollback"
        observation_status = "failed"
    else:
        recommendation = "pause"
        observation_status = "warning"
    observation = TakeoverObservation(
        tenant_id=project.tenant_id,
        project_id=project.id,
        route_version_id=route.id,
        checked_url=str(data["checked_url"]),
        status=observation_status,
        success_rate=success_rate,
        error_rate=error_rate,
        latency_ms=data.get("latency_ms"),
        h5_reach_rate=float(data.get("h5_reach_rate", 0)),
        target_match=target_match,
        metrics=data.get("metrics") or {},
        recommendation=recommendation,
    )
    db.add(observation)
    await db.flush()
    project.status = TakeoverProjectStatus.observing
    await write_audit_log(
        db,
        str(account_id),
        str(project.tenant_id),
        "takeover_observation_recorded",
        f"takeover_route:{route.id}",
        {"recommendation": recommendation},
    )
    return observation


async def complete_route(
    db: AsyncSession, project: TakeoverProject, route: TakeoverRouteVersion, account_id: uuid.UUID
) -> TakeoverRouteVersion:
    latest = await db.scalar(
        select(TakeoverObservation)
        .where(TakeoverObservation.route_version_id == route.id)
        .order_by(TakeoverObservation.created_at.desc())
        .limit(1)
    )
    if not latest or latest.recommendation != "continue":
        raise HTTPException(status_code=409, detail="尚无通过的观察结果，不能完成接管")
    project.status = TakeoverProjectStatus.completed
    route.status = TakeoverRouteStatus.active
    await _write_event(
        db, project, route, account_id, "complete", "completed", details={"observation_id": str(latest.id)}
    )
    await db.flush()
    return route


async def rollback_route(
    db: AsyncSession,
    project: TakeoverProject,
    route: TakeoverRouteVersion,
    account_id: uuid.UUID,
    reason: str,
    idempotency_key: str | None,
) -> TakeoverRouteVersion:
    existing = await _event_exists(db, project, idempotency_key)
    if existing and existing.route_version_id == route.id:
        return route
    if route.status not in {TakeoverRouteStatus.active, TakeoverRouteStatus.paused, TakeoverRouteStatus.confirmed}:
        raise HTTPException(status_code=409, detail="当前路由版本不能回退")
    route.status = TakeoverRouteStatus.rolled_back
    route.rollback_reason = reason
    project.active_route_version_id = None
    project.status = TakeoverProjectStatus.rolled_back
    aliases = list(
        (
            await db.scalars(
                select(TakeoverAlias).where(
                    TakeoverAlias.project_id == project.id, TakeoverAlias.status == TakeoverAliasStatus.active
                )
            )
        ).all()
    )
    for alias in aliases:
        alias.status = TakeoverAliasStatus.staged
    await _write_event(
        db,
        project,
        route,
        account_id,
        "rollback",
        "rolled_back",
        key=idempotency_key,
        details={"reason": reason, "fallback_url": project.fallback_url},
    )
    await db.flush()
    return route


def _render_route_target(route: TakeoverRouteVersion, alias: TakeoverAlias, code: str) -> str | None:
    """将确认过的路由目标绑定到真实业务码，避免所有旧码落到同一个样本页。"""
    target = unquote(route.target_url)
    public_id = getattr(alias, "_target_public_id", None)
    if "{public_id}" in target and not public_id:
        return None
    return target.replace("{public_id}", str(public_id or "")).replace("{legacy_code}", code)


async def gateway_resolve(db: AsyncSession, project: TakeoverProject, raw_url: str, *, preview: bool = False) -> dict:
    route = await db.scalar(
        select(TakeoverRouteVersion).where(
            TakeoverRouteVersion.project_id == project.id,
            TakeoverRouteVersion.tenant_id == project.tenant_id,
            TakeoverRouteVersion.status == TakeoverRouteStatus.active,
        )
    )
    if not route:
        return {"action": "fallback", "status": "pending_external", "redirect_to": project.fallback_url}
    alias = await resolve_alias(db, project, raw_url, include_staged=preview)
    code = extract_legacy_code(project, raw_url)
    selected = not route.sample_codes and not route.code_prefix
    if route.sample_codes:
        selected = code in set(route.sample_codes)
    if route.code_prefix:
        selected = selected or code.startswith(normalize_legacy_code(route.code_prefix))
    if alias and selected:
        target = _render_route_target(route, alias, code)
        if target is None:
            return {
                "action": "fallback",
                "status": "target_unavailable",
                "legacy_code": code,
                "redirect_to": project.fallback_url,
            }
        return {
            "action": "new_chain",
            "status": "resolved",
            "legacy_code": code,
            "redirect_to": target,
            "target": {
                "public_id": getattr(alias, "_target_public_id", None),
                "product_id": alias.product_id,
                "production_batch_id": alias.production_batch_id,
            },
        }
    return {"action": "fallback", "status": "resolved", "legacy_code": code, "redirect_to": project.fallback_url}


async def build_takeover_launch_gate_check(db: AsyncSession, tenant_id: uuid.UUID) -> dict | None:
    """把完整双模式接管完成后才开放给通用上线门禁。

    没有接管项目的租户保持“不适用”，不会影响普通上线；一旦租户创建了接管项目，
    必须同时有旧系统跳转和 CNAME 两条已完成链路，且历史项目不能处于中间状态。
    """
    projects = list((await db.scalars(select(TakeoverProject).where(TakeoverProject.tenant_id == tenant_id))).all())
    if not projects:
        return None
    completed_modes = {
        str(project.mode) for project in projects if str(project.status) == TakeoverProjectStatus.completed
    }
    required_modes = {TakeoverMode.legacy_redirect.value, TakeoverMode.cname.value}
    passed = completed_modes >= required_modes and all(
        str(project.status) in {TakeoverProjectStatus.completed, TakeoverProjectStatus.rolled_back}
        for project in projects
    )
    return _check(
        "takeover_closed_loop",
        "既有码双模式接管闭环已通过",
        passed,
        "旧系统跳转与 CNAME 两条链路均已完成独立验收"
        if passed
        else "既有码接管已启用，但两种模式、回退和观察必须全部完成后才能进入通用上线门禁",
    )
