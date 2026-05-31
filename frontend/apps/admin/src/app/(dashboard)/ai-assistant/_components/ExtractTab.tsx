"use client";

import { useState } from "react";
import { App, Button, Descriptions, Form, Input, Select, Spin } from "antd";
import { ExperimentOutlined, CheckCircleOutlined } from "@ant-design/icons";
import ImageUploadInput from "@/components/ImageUploadInput";
import { extractFromText, recognizeImage, getAIErrorMessage, type ExtractResult } from "@/lib/ai";
import { ResultCard } from "./shared";
import { FIELD_LABELS } from "./constants";

const { TextArea } = Input;

export function ExtractTab() {
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
          <Select value={mode} onChange={(v) => { setMode(v); setResult(null); form.resetFields(); }}
            options={[{ value: "text", label: "文本提取" }, { value: "image", label: "图片识别" }]}
          />
        </Form.Item>
        {mode === "text" ? (
          <Form.Item name="text" label="产品描述文本" rules={[{ required: true, message: "请输入产品描述文本" }]}>
            <TextArea rows={6} placeholder="粘贴产品包装上的文字描述、配料表、营养成分表等内容..." />
          </Form.Item>
        ) : (
          <>
            <Form.Item
              name="image_url"
              label="产品图片"
              rules={[
                { required: true, message: "请上传或填写图片地址" },
                { type: "url", message: "请输入以 http:// 或 https:// 开头的图片链接" },
              ]}
            >
              <ImageUploadInput module="ai-recognition" previewAlt="待识别图片预览" />
            </Form.Item>
            <Form.Item name="filename" label="文件名"><Input placeholder="image.jpg" /></Form.Item>
          </>
        )}
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={loading} icon={<ExperimentOutlined />}>开始识别</Button>
        </Form.Item>
      </Form>
      {loading && <div className="py-8 text-center"><Spin size="large" /><div className="mt-2 text-gray-500">AI 正在分析中...</div></div>}
      {result && !loading && (
        <ResultCard title="识别结果" icon={<CheckCircleOutlined />} generationId={result.generation_id}>
          <Descriptions bordered column={1} size="small">
            {Object.entries(result.fields).map(([key, value]) => {
              if (value == null || value === "") return null;
              const label = FIELD_LABELS[key] || key;
              const display = key === "confidence" ? `${Math.round((value as number) * 100)}%` : String(value);
              return <Descriptions.Item key={key} label={label}>{display}</Descriptions.Item>;
            })}
          </Descriptions>
        </ResultCard>
      )}
    </div>
  );
}
