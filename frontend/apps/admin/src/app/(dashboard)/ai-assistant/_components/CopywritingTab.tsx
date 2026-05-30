"use client";

import { useState } from "react";
import { App, Button, Col, Form, Input, Row, Select, Spin, Typography } from "antd";
import { CopyOutlined, CheckCircleOutlined } from "@ant-design/icons";
import { generateCopywriting, getAIErrorMessage, type CopywritingResult, type CopywritingType } from "@/lib/ai";
import { ResultCard } from "./shared";

const { Paragraph } = Typography;

export function CopywritingTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<CopywritingResult | null>(null);
  const [form] = Form.useForm();

  const handleSubmit = async (values: { type: CopywritingType; product_name: string; keywords: string }) => {
    setLoading(true);
    setResult(null);
    try {
      const keywords = values.keywords ? values.keywords.split(/[,，、\s]+/).filter(Boolean) : [];
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
      return <Paragraph style={{ whiteSpace: "pre-wrap", lineHeight: 1.8 }} className="text-gray-700">{content}</Paragraph>;
    }
    return (
      <ul className="list-inside list-disc space-y-2 pl-2">
        {content.items.map((item, idx) => <li key={idx} className="text-gray-700">{item}</li>)}
      </ul>
    );
  };

  return (
    <div>
      <Form form={form} layout="vertical" onFinish={handleSubmit} initialValues={{ type: "brand_story" }}>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="type" label="文案类型" rules={[{ required: true, message: "请选择文案类型" }]}>
              <Select options={[{ value: "brand_story", label: "品牌故事" }, { value: "selling_points", label: "产品卖点" }]} />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="product_name" label="产品名称" rules={[{ required: true, message: "请输入产品名称" }]}>
              <Input placeholder="例: 有机五常大米" />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="keywords" label="关键词（用逗号分隔）"><Input placeholder="例: 有机, 五常, 大米, 绿色食品" /></Form.Item>
        <Form.Item><Button type="primary" htmlType="submit" loading={loading} icon={<CopyOutlined />}>生成文案</Button></Form.Item>
      </Form>
      {loading && <div className="py-8 text-center"><Spin size="large" /><div className="mt-2 text-gray-500">AI 正在创作中...</div></div>}
      {result && !loading && (
        <ResultCard title="文案结果" icon={<CheckCircleOutlined />} generationId={result.generation_id}>
          {renderContent(result.content)}
        </ResultCard>
      )}
    </div>
  );
}
