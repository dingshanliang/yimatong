"use client";

import { useState } from "react";
import { App, Button, Col, Descriptions, Form, Input, Row, Select, Spin } from "antd";
import { GiftOutlined, CheckCircleOutlined } from "@ant-design/icons";
import { generateCampaign, getAIErrorMessage, type CampaignResult, type CampaignGoal } from "@/lib/ai";
import { ResultCard } from "./shared";

const CAMPAIGN_FIELDS: Record<string, string> = {
  name: "活动名称", description: "活动描述", duration: "活动周期",
  rules: "参与规则", prizes: "奖品设置", mechanics: "活动机制",
  budget: "预估预算", kpi: "预期效果",
};

export function CampaignTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<CampaignResult | null>(null);
  const [form] = Form.useForm();

  const handleSubmit = async (values: { product_name: string; goal: CampaignGoal; target_audience: string }) => {
    setLoading(true);
    setResult(null);
    try {
      const data = await generateCampaign(values.product_name, values.goal, values.target_audience);
      setResult(data);
      message.success("活动方案生成完成");
    } catch (err) { message.error(getAIErrorMessage(err)); }
    finally { setLoading(false); }
  };

  return (
    <div>
      <Form form={form} layout="vertical" onFinish={handleSubmit} initialValues={{ goal: "promotion" }}>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="product_name" label="产品名称" rules={[{ required: true, message: "请输入产品名称" }]}>
              <Input placeholder="例: 有机五常大米" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="goal" label="活动目标" rules={[{ required: true, message: "请选择活动目标" }]}>
              <Select options={[
                { value: "promotion", label: "拉新推广" }, { value: "retention", label: "复购留存" },
                { value: "brand_awareness", label: "品牌认知" }, { value: "festival", label: "节日活动" },
              ]} />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="target_audience" label="目标受众" rules={[{ required: true, message: "请描述目标受众" }]}>
          <Input placeholder="例: 25-40岁注重健康的都市白领女性" />
        </Form.Item>
        <Form.Item><Button type="primary" htmlType="submit" loading={loading} icon={<GiftOutlined />}>生成活动方案</Button></Form.Item>
      </Form>
      {loading && <div className="py-8 text-center"><Spin size="large" /><div className="mt-2 text-gray-500">AI 正在策划中...</div></div>}
      {result && !loading && (
        <ResultCard title="活动方案" icon={<CheckCircleOutlined />} generationId={result.generation_id}>
          <Descriptions bordered column={1} size="small">
            {Object.entries(result.campaign).filter(([, v]) => v != null && v !== "").map(([key, value]) => (
              <Descriptions.Item key={key} label={CAMPAIGN_FIELDS[key] || key}>
                {Array.isArray(value) ? (
                  <ul className="list-inside list-disc pl-2">{value.map((item, idx) => <li key={idx}>{String(item)}</li>)}</ul>
                ) : <span style={{ whiteSpace: "pre-wrap" }}>{String(value)}</span>}
              </Descriptions.Item>
            ))}
          </Descriptions>
        </ResultCard>
      )}
    </div>
  );
}
