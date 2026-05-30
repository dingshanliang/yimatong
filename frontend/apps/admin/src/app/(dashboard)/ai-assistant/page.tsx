"use client";

import React, { useState } from "react";
import {
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Form,
  Input,
  Row,
  Select,
  Spin,
  Tabs,
  Tag,
  Typography,
} from "antd";
import {
  CopyOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  GiftOutlined,
  BulbOutlined,
  CheckCircleOutlined,
} from "@ant-design/icons";
import type { TabsProps } from "antd";
import {
  extractFromText,
  recognizeImage,
  generateCopywriting,
  generatePageCopy,
  suggestPageStructure,
  generateCampaign,
  getAIErrorMessage,
  type ExtractResult,
  type CopywritingResult,
  type PageCopyResult,
  type PageSuggestResult,
  type CampaignResult,
  type CopywritingType,
  type CampaignGoal,
} from "@/lib/ai";

const { Title, Paragraph, Text } = Typography;
const { TextArea } = Input;

// ──────────────────── Shared Result Display ────────────────────

function GenerationIdTag({ id }: { id: string }) {
  return (
    <Tag color="blue" className="mt-2">
      生成 ID: {id}
    </Tag>
  );
}

function ResultCard({
  title,
  icon,
  children,
  generationId,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  generationId?: string;
}) {
  return (
    <Card
      className="mt-4"
      title={
        <span>
          {icon} {title}
        </span>
      }
    >
      {children}
      {generationId && <GenerationIdTag id={generationId} />}
    </Card>
  );
}

// ──────────────────── Field Labels Map ────────────────────

const FIELD_LABELS: Record<string, string> = {
  product_name: "产品名称",
  origin: "产地",
  weight: "净含量/规格",
  shelf_life: "保质期",
  category: "品类",
  confidence: "置信度",
  source: "数据来源",
};

// ──────────────────── Tab 1: 资料识别 ────────────────────

function ExtractTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ExtractResult | null>(null);
  const [mode, setMode] = useState<"text" | "image">("text");
  const [form] = Form.useForm();

  const handleSubmit = async (values: { text?: string; image_url?: string; filename?: string }) => {
    setLoading(true);
    setResult(null);
    try {
      if (mode === "text") {
        const data = await extractFromText(values.text || "");
        setResult(data);
        message.success("文本提取完成");
      } else {
        const data = await recognizeImage(values.image_url || "", values.filename || "image.jpg");
        setResult(data);
        message.success("图片识别完成");
      }
    } catch (err) {
      message.error(getAIErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <Form form={form} layout="vertical" onFinish={handleSubmit}>
        <Form.Item label="识别方式">
          <Select
            value={mode}
            onChange={(v) => {
              setMode(v);
              setResult(null);
              form.resetFields();
            }}
            options={[
              { value: "text", label: "文本提取" },
              { value: "image", label: "图片识别" },
            ]}
          />
        </Form.Item>

        {mode === "text" ? (
          <Form.Item
            name="text"
            label="产品描述文本"
            rules={[{ required: true, message: "请输入产品描述文本" }]}
          >
            <TextArea
              rows={6}
              placeholder="粘贴产品包装上的文字描述、配料表、营养成分表等内容..."
            />
          </Form.Item>
        ) : (
          <>
            <Form.Item
              name="image_url"
              label="图片 URL"
              rules={[{ required: true, message: "请输入图片 URL" }]}
            >
              <Input placeholder="已上传到 MinIO 的图片 URL" />
            </Form.Item>
            <Form.Item name="filename" label="文件名">
              <Input placeholder="image.jpg" />
            </Form.Item>
          </>
        )}

        <Form.Item>
          <Button type="primary" htmlType="submit" loading={loading} icon={<ExperimentOutlined />}>
            开始识别
          </Button>
        </Form.Item>
      </Form>

      {loading && (
        <div className="py-8 text-center">
          <Spin size="large" />
          <div className="mt-2 text-gray-500">AI 正在分析中...</div>
        </div>
      )}

      {result && !loading && (
        <ResultCard title="识别结果" icon={<CheckCircleOutlined />} generationId={result.generation_id}>
          <Descriptions bordered column={1} size="small">
            {Object.entries(result.fields).map(([key, value]) => {
              if (value == null || value === "") return null;
              const label = FIELD_LABELS[key] || key;
              const display =
                key === "confidence" ? `${Math.round((value as number) * 100)}%` : String(value);
              return (
                <Descriptions.Item key={key} label={label}>
                  {display}
                </Descriptions.Item>
              );
            })}
          </Descriptions>
        </ResultCard>
      )}
    </div>
  );
}

// ──────────────────── Tab 2: 文案生成 ────────────────────

function CopywritingTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<CopywritingResult | null>(null);
  const [form] = Form.useForm();

  const handleSubmit = async (values: {
    type: CopywritingType;
    product_name: string;
    keywords: string;
  }) => {
    setLoading(true);
    setResult(null);
    try {
      const keywords = values.keywords
        ? values.keywords.split(/[,，、\s]+/).filter(Boolean)
        : [];
      const data = await generateCopywriting(values.type, values.product_name, keywords);
      setResult(data);
      message.success("文案生成完成");
    } catch (err) {
      message.error(getAIErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const renderContent = (content: string | { items: string[] }) => {
    if (typeof content === "string") {
      return (
        <Paragraph
          style={{ whiteSpace: "pre-wrap", lineHeight: 1.8 }}
          className="text-gray-700"
        >
          {content}
        </Paragraph>
      );
    }
    return (
      <ul className="list-inside list-disc space-y-2 pl-2">
        {content.items.map((item, idx) => (
          <li key={idx} className="text-gray-700">
            {item}
          </li>
        ))}
      </ul>
    );
  };

  return (
    <div>
      <Form form={form} layout="vertical" onFinish={handleSubmit} initialValues={{ type: "brand_story" }}>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item
              name="type"
              label="文案类型"
              rules={[{ required: true, message: "请选择文案类型" }]}
            >
              <Select
                options={[
                  { value: "brand_story", label: "品牌故事" },
                  { value: "selling_points", label: "产品卖点" },
                ]}
              />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              name="product_name"
              label="产品名称"
              rules={[{ required: true, message: "请输入产品名称" }]}
            >
              <Input placeholder="例: 有机五常大米" />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="keywords" label="关键词（用逗号分隔）">
          <Input placeholder="例: 有机, 五常, 大米, 绿色食品" />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={loading} icon={<CopyOutlined />}>
            生成文案
          </Button>
        </Form.Item>
      </Form>

      {loading && (
        <div className="py-8 text-center">
          <Spin size="large" />
          <div className="mt-2 text-gray-500">AI 正在创作中...</div>
        </div>
      )}

      {result && !loading && (
        <ResultCard title="文案结果" icon={<CheckCircleOutlined />} generationId={result.generation_id}>
          {renderContent(result.content)}
        </ResultCard>
      )}
    </div>
  );
}

// ──────────────────── Tab 3: 页面方案 ────────────────────

function PagePlanTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [pageCopyResult, setPageCopyResult] = useState<PageCopyResult | null>(null);
  const [suggestResult, setSuggestResult] = useState<PageSuggestResult | null>(null);
  const [activeSubTab, setActiveSubTab] = useState<"copy" | "suggest">("copy");
  const [form] = Form.useForm();

  const handlePageCopy = async (values: {
    product_name: string;
    category: string;
    keywords: string;
  }) => {
    setLoading(true);
    setPageCopyResult(null);
    try {
      const keywords = values.keywords
        ? values.keywords.split(/[,，、\s]+/).filter(Boolean)
        : [];
      const data = await generatePageCopy(values.product_name, values.category, keywords);
      setPageCopyResult(data);
      message.success("页面文案方案生成完成");
    } catch (err) {
      message.error(getAIErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const handlePageSuggest = async (values: { product_name: string; category: string }) => {
    setLoading(true);
    setSuggestResult(null);
    try {
      const data = await suggestPageStructure(values.product_name, values.category);
      setSuggestResult(data);
      message.success("页面结构建议生成完成");
    } catch (err) {
      message.error(getAIErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (values: {
    product_name: string;
    category: string;
    keywords?: string;
  }) => {
    if (activeSubTab === "copy") {
      handlePageCopy(values as Parameters<typeof handlePageCopy>[0]);
    } else {
      handlePageSuggest(values);
    }
  };

  return (
    <div>
      <div className="mb-4 flex gap-2">
        <Button
          type={activeSubTab === "copy" ? "primary" : "default"}
          icon={<FileTextOutlined />}
          onClick={() => setActiveSubTab("copy")}
        >
          页面文案
        </Button>
        <Button
          type={activeSubTab === "suggest" ? "primary" : "default"}
          icon={<BulbOutlined />}
          onClick={() => setActiveSubTab("suggest")}
        >
          结构建议
        </Button>
      </div>

      <Form
        form={form}
        layout="vertical"
        onFinish={handleSubmit}
        initialValues={{ category: "其他" }}
      >
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item
              name="product_name"
              label="产品名称"
              rules={[{ required: true, message: "请输入产品名称" }]}
            >
              <Input placeholder="例: 有机五常大米" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              name="category"
              label="品类"
              rules={[{ required: true, message: "请选择品类" }]}
            >
              <Select
                showSearch
                allowClear
                placeholder="选择或输入品类"
                options={[
                  { value: "大米", label: "大米" },
                  { value: "面粉", label: "面粉" },
                  { value: "食用油", label: "食用油" },
                  { value: "茶叶", label: "茶叶" },
                  { value: "水果", label: "水果" },
                  { value: "蔬菜", label: "蔬菜" },
                  { value: "肉类", label: "肉类" },
                  { value: "乳制品", label: "乳制品" },
                  { value: "酒类", label: "酒类" },
                  { value: "饮料", label: "饮料" },
                  { value: "零食", label: "零食" },
                  { value: "保健品", label: "保健品" },
                  { value: "其他", label: "其他" },
                ]}
              />
            </Form.Item>
          </Col>
        </Row>
        {activeSubTab === "copy" && (
          <Form.Item name="keywords" label="关键词（用逗号分隔）">
            <Input placeholder="例: 有机, 五常, 绿色食品" />
          </Form.Item>
        )}
        <Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={loading}
            icon={activeSubTab === "copy" ? <FileTextOutlined /> : <BulbOutlined />}
          >
            {activeSubTab === "copy" ? "生成页面文案" : "生成结构建议"}
          </Button>
        </Form.Item>
      </Form>

      {loading && (
        <div className="py-8 text-center">
          <Spin size="large" />
          <div className="mt-2 text-gray-500">AI 正在分析中...</div>
        </div>
      )}

      {pageCopyResult && !loading && activeSubTab === "copy" && (
        <ResultCard
          title="页面文案方案"
          icon={<CheckCircleOutlined />}
          generationId={pageCopyResult.generation_id}
        >
          <Title level={5}>品牌故事</Title>
          <Paragraph style={{ whiteSpace: "pre-wrap", lineHeight: 1.8 }} className="text-gray-700">
            {pageCopyResult.result.copywriting.brand_story}
          </Paragraph>

          <Divider />
          <Title level={5}>产品卖点</Title>
          <ul className="list-inside list-disc space-y-2 pl-2">
            {pageCopyResult.result.copywriting.selling_points.map((sp, idx) => (
              <li key={idx} className="text-gray-700">
                <Text strong>{sp.title}</Text>：{sp.detail}
              </li>
            ))}
          </ul>

          <Divider />
          <Title level={5}>推荐模板</Title>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="模板类型">
              {pageCopyResult.result.recommended_template.template_type}
            </Descriptions.Item>
            <Descriptions.Item label="模板名称">
              {pageCopyResult.result.recommended_template.name}
            </Descriptions.Item>
          </Descriptions>

          <Divider />
          <Title level={5}>推荐页面模块</Title>
          <div className="flex flex-wrap gap-2">
            {pageCopyResult.result.page_suggestion.modules.map((mod) => (
              <Tag key={mod.id} color={mod.enabled ? "blue" : "default"}>
                {mod.type}
              </Tag>
            ))}
          </div>
        </ResultCard>
      )}

      {suggestResult && !loading && activeSubTab === "suggest" && (
        <ResultCard
          title="页面结构建议"
          icon={<CheckCircleOutlined />}
          generationId={suggestResult.generation_id}
        >
          {suggestResult.suggestion.modules && suggestResult.suggestion.modules.length > 0 ? (
            <div>
              <Text strong>推荐模块结构：</Text>
              <div className="mt-2 flex flex-wrap gap-2">
                {suggestResult.suggestion.modules.map((mod, idx) => (
                  <Tag key={idx} color="blue">
                    {mod}
                  </Tag>
                ))}
              </div>
            </div>
          ) : (
            <Descriptions bordered column={1} size="small">
              {Object.entries(suggestResult.suggestion)
                .filter(([, v]) => v != null && v !== "")
                .map(([key, value]) => (
                  <Descriptions.Item key={key} label={key}>
                    {typeof value === "object" ? JSON.stringify(value, null, 2) : String(value)}
                  </Descriptions.Item>
                ))}
            </Descriptions>
          )}
        </ResultCard>
      )}
    </div>
  );
}

// ──────────────────── Tab 4: 活动方案 ────────────────────

function CampaignTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<CampaignResult | null>(null);
  const [form] = Form.useForm();

  const handleSubmit = async (values: {
    product_name: string;
    goal: CampaignGoal;
    target_audience: string;
  }) => {
    setLoading(true);
    setResult(null);
    try {
      const data = await generateCampaign(values.product_name, values.goal, values.target_audience);
      setResult(data);
      message.success("活动方案生成完成");
    } catch (err) {
      message.error(getAIErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const CAMPAIGN_FIELDS: Record<string, string> = {
    name: "活动名称",
    description: "活动描述",
    duration: "活动周期",
    rules: "参与规则",
    prizes: "奖品设置",
    mechanics: "活动机制",
    budget: "预估预算",
    kpi: "预期效果",
  };

  return (
    <div>
      <Form form={form} layout="vertical" onFinish={handleSubmit} initialValues={{ goal: "promotion" }}>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item
              name="product_name"
              label="产品名称"
              rules={[{ required: true, message: "请输入产品名称" }]}
            >
              <Input placeholder="例: 有机五常大米" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              name="goal"
              label="活动目标"
              rules={[{ required: true, message: "请选择活动目标" }]}
            >
              <Select
                options={[
                  { value: "promotion", label: "拉新推广" },
                  { value: "retention", label: "复购留存" },
                  { value: "brand_awareness", label: "品牌认知" },
                  { value: "festival", label: "节日活动" },
                ]}
              />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item
          name="target_audience"
          label="目标受众"
          rules={[{ required: true, message: "请描述目标受众" }]}
        >
          <Input placeholder="例: 25-40岁注重健康的都市白领女性" />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={loading} icon={<GiftOutlined />}>
            生成活动方案
          </Button>
        </Form.Item>
      </Form>

      {loading && (
        <div className="py-8 text-center">
          <Spin size="large" />
          <div className="mt-2 text-gray-500">AI 正在策划中...</div>
        </div>
      )}

      {result && !loading && (
        <ResultCard
          title="活动方案"
          icon={<CheckCircleOutlined />}
          generationId={result.generation_id}
        >
          <Descriptions bordered column={1} size="small">
            {Object.entries(result.campaign)
              .filter(([, v]) => v != null && v !== "")
              .map(([key, value]) => (
                <Descriptions.Item key={key} label={CAMPAIGN_FIELDS[key] || key}>
                  {Array.isArray(value) ? (
                    <ul className="list-inside list-disc pl-2">
                      {value.map((item, idx) => (
                        <li key={idx}>{String(item)}</li>
                      ))}
                    </ul>
                  ) : (
                    <span style={{ whiteSpace: "pre-wrap" }}>{String(value)}</span>
                  )}
                </Descriptions.Item>
              ))}
          </Descriptions>
        </ResultCard>
      )}
    </div>
  );
}

// ──────────────────── Main Page ────────────────────

const tabItems: TabsProps["items"] = [
  {
    key: "extract",
    label: (
      <span>
        <ExperimentOutlined /> 资料识别
      </span>
    ),
    children: <ExtractTab />,
  },
  {
    key: "copywriting",
    label: (
      <span>
        <CopyOutlined /> 文案生成
      </span>
    ),
    children: <CopywritingTab />,
  },
  {
    key: "page-plan",
    label: (
      <span>
        <FileTextOutlined /> 页面方案
      </span>
    ),
    children: <PagePlanTab />,
  },
  {
    key: "campaign",
    label: (
      <span>
        <GiftOutlined /> 活动方案
      </span>
    ),
    children: <CampaignTab />,
  },
];

export default function AIAssistantPage() {
  return (
    <div>
      <div className="mb-4">
        <Title level={4} className="!mb-1">
          AI 助手
        </Title>
        <Text type="secondary">
          利用 AI 智能识别产品信息、生成营销文案、推荐页面方案和策划活动方案
        </Text>
      </div>
      <Tabs items={tabItems} size="large" />
    </div>
  );
}
