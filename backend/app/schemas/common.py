from pydantic import BaseModel, Field


class PaginatedResponse(BaseModel):
    items: list = Field(..., description="当前页数据列表")
    total: int = Field(..., description="总记录数", examples=[100])
    page: int = Field(..., description="当前页码", examples=[1])
    page_size: int = Field(..., description="每页条数", examples=[20])


class ErrorDetail(BaseModel):
    detail: str = Field(..., description="错误描述信息", examples=["资源不存在"])


class ValidationErrorDetail(BaseModel):
    loc: list[str] = Field(
        ...,
        description="错误字段路径",
        examples=[["body", "email"]],
    )
    msg: str = Field(
        ...,
        description="错误提示信息",
        examples=["field required"],
    )
    type: str = Field(
        ...,
        description="错误类型标识",
        examples=["missing"],
    )


class HTTPValidationError(BaseModel):
    detail: list[ValidationErrorDetail] = Field(
        ...,
        description="校验错误详情列表",
    )


# 预定义的常用错误响应示例
UNAUTHORIZED_EXAMPLE = {"detail": "Missing or invalid token"}
FORBIDDEN_EXAMPLE = {"detail": "Permission denied"}
NOT_FOUND_EXAMPLE = {"detail": "Resource not found"}
CONFLICT_EXAMPLE = {"detail": "Resource already exists"}
TOO_MANY_REQUESTS_EXAMPLE = {"detail": "Too many requests"}


class StandardErrorResponse(BaseModel):
    error_code: str = Field(..., description="Machine-readable error identifier", examples=["NOT_FOUND"])
    detail: str | list[ValidationErrorDetail] = Field(
        ..., description="Human-readable message or validation errors"
    )
    request_id: str | None = Field(None, description="Request tracing ID")
