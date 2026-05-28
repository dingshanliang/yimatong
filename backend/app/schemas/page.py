"""页面模板配置 schema 校验"""

from pydantic import BaseModel, Field


class TemplateType:
    product_info = "product_info"
    traceability = "traceability"
    brand_story = "brand_story"


class ProductInfoConfig(BaseModel):
    brand_name: str = Field(..., min_length=1, max_length=100)
    brand_logo: str | None = None
    product_name: str = Field(..., min_length=1, max_length=200)
    product_image: str | None = None
    specifications: dict | None = None
    batch_info: dict | None = None


class TraceabilityConfig(BaseModel):
    brand_name: str = Field(..., min_length=1, max_length=100)
    product_name: str = Field(..., min_length=1, max_length=200)
    trace_nodes: list[dict] | None = None


class BrandStoryConfig(BaseModel):
    brand_name: str = Field(..., min_length=1, max_length=100)
    story_title: str | None = None
    story_content: str | None = None
    cover_image: str | None = None


SCHEMA_MAP = {
    TemplateType.product_info: ProductInfoConfig,
    TemplateType.traceability: TraceabilityConfig,
    TemplateType.brand_story: BrandStoryConfig,
}


def validate_template_config(template_type: str, data: dict) -> dict:
    if template_type not in SCHEMA_MAP:
        raise ValueError(f"Unknown template type: {template_type}")
    schema_cls = SCHEMA_MAP[template_type]
    validated = schema_cls.model_validate(data)
    return validated.model_dump()
