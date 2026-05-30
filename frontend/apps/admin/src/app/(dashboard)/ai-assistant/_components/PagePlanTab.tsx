"use client";

import { useState } from "react";
import { App, Button, Col, Descriptions, Divider, Form, Input, Row, Select, Spin, Tag, Typography } from "antd";
import { FileTextOutlined, BulbOutlined, CheckCircleOutlined } from "@ant-design/icons";
import { generatePageCopy, suggestPageStructure, getAIErrorMessage, type PageCopyResult, type PageSuggestResult } from "@/lib/ai";
import { ResultCard } from "./shared";

const { Title, Text, Paragraph } = Typography;

export function PagePlanTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [pageCopyResult, setPageCopyResult] = useState<PageCopyResult | null>(null);
  const [suggestResult, setSuggestResult] = useState<PageSuggestResult | null>(null);
  const [activeSubTab, setActiveSubTab] = useState<"copy" | "suggest">("copy");
  const [form] = Form.useForm();

  const handlePageCopy = async (values: { product_name: string; category: string; keywords: string }) => {
    setLoading(true);
    setPageCopyResult(null);
    try {
      const keywords = values.keywords ? values.keywords.split(/[,，、\s]+/).filter(Boolean) : [];
      const data = await generatePageCopy(values.product_name, values.category, keywords);
      setPageCopyResult(data);
      message.success("页面文案方案生成完成");
    } catch (err) { message.error(getAIErrorMessage(err)); }
    finally { setLoading(false); }
  };

  const handlePageSuggest = async (values: { product_name: string; category: string }) => {
    setLoading(true);
    setSuggestResult(null);
    try {
      const data = await suggestPageStructure(values.product_name, values.category);
      setSuggestResult(data);
      message.success("页面结构建议生成完成");
    } catch (err) { message.error(getAIErrorMessage(err)); }
    finally { setLoading(false); }
  };

  const handleSubmit = (values: { product_name: string; category: string; keywords?: string }) => {
    if (activeSubTab === "copy") {
      handlePageCopy(values as Parameters<typeof handlePageCopy>[0]);
    } else {
      handlePageSuggest(values);
    }
  };

  return (
    <div>
      <div className="mb-4 flex gap-2">
        <Button type={activeSubTab === "copy" ? "primary" : "default"} icon={<FileTextOutlined />} onClick={() => setActiveSubTab("copy")}>页面文案</Button>
        <Button type={activeSubTab === "suggest" ? "primary" : "default"} icon={<BulbOutlined />} onClick={() => setActiveSubTab("suggest")}>结构建议</Button>
      </div>
      <Form form={form} layout="vertical" onFinish={handleSubmit} initialValues={{ category: "其他" }}>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="product_name" label="产品名称" rules={[{ required: true, message: "请输入产品名称" }]}>
              <Input placeholder="例: 有机五常大米" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="category" label="品类" rules={[{ required: true, message: "请选择品类" }]}>
              <Select showSearch allowClear placeholder="选择或输入品类"
                options={["大米","面粉","食用油","茶叶","水果","蔬菜","肉类","乳制品","酒类","饮料","零食","保健品","其他"].map(v => ({ value: v, label: v }))}
              />
            </Form.Item>
          </Col>
        </Row>
        {activeSubTab === "copy" && (
          <Form.Item name="keywords" label="关键词（用逗号分隔）"><Input placeholder="例: 有机, 五常, 绿色食品" /></Form.Item>
        )}
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={loading} icon={activeSubTab === "copy" ? <FileTextOutlined /> : <BulbOutlined />}>
            {activeSubTab === "copy" ? "生成页面文案" : "生成结构建议"}
          </Button>
        </Form.Item>
      </Form>
      {loading && <div className="py-8 text-center"><Spin size="large" /><div className="mt-2 text-gray-500">AI 正在分析中...</div></div>}
      {pageCopyResult && !loading && activeSubTab === "copy" && (
        <ResultCard title="页面文案方案" icon={<CheckCircleOutlined />} generationId={pageCopyResult.generation_id}>
          <Title level={5}>品牌故事</Title>
          <Paragraph style={{ whiteSpace: "pre-wrap", lineHeight: 1.8 }} className="text-gray-700">{pageCopyResult.result.copywriting.brand_story}</Paragraph>
          <Divider />
          <Title level={5}>产品卖点</Title>
          <ul className="list-inside list-disc space-y-2 pl-2">
            {pageCopyResult.result.copywriting.selling_points.map((sp, idx) => (
              <li key={idx} className="text-gray-700"><Text strong>{sp.title}</Text>：{sp.detail}</li>
            ))}
          </ul>
          <Divider />
          <Title level={5}>推荐模板</Title>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="模板类型">{pageCopyResult.result.recommended_template.template_type}</Descriptions.Item>
            <Descriptions.Item label="模板名称">{pageCopyResult.result.recommended_template.name}</Descriptions.Item>
          </Descriptions>
          <Divider />
          <Title level={5}>推荐页面模块</Title>
          <div className="flex flex-wrap gap-2">
            {pageCopyResult.result.page_suggestion.modules.map((mod) => (
              <Tag key={mod.id} color={mod.enabled ? "blue" : "default"}>{mod.type}</Tag>
            ))}
          </div>
        </ResultCard>
      )}
      {suggestResult && !loading && activeSubTab === "suggest" && (
        <ResultCard title="页面结构建议" icon={<CheckCircleOutlined />} generationId={suggestResult.generation_id}>
          {suggestResult.suggestion.modules && suggestResult.suggestion.modules.length > 0 ? (
            <div>
              <Text strong>推荐模块结构：</Text>
              <div className="mt-2 flex flex-wrap gap-2">
                {suggestResult.suggestion.modules.map((mod, idx) => <Tag key={idx} color="blue">{mod}</Tag>)}
              </div>
            </div>
          ) : (
            <Descriptions bordered column={1} size="small">
              {Object.entries(suggestResult.suggestion).filter(([, v]) => v != null && v !== "").map(([key, value]) => (
                <Descriptions.Item key={key} label={key}>{typeof value === "object" ? JSON.stringify(value, null, 2) : String(value)}</Descriptions.Item>
              ))}
            </Descriptions>
          )}
        </ResultCard>
      )}
    </div>
  );
}
