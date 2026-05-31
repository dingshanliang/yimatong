"use client";

import { useState } from "react";
import { App, Alert, Button, Divider, Drawer, Input, List, Tag, Typography, Upload } from "antd";
import { CameraOutlined, FileTextOutlined, BulbOutlined, PlusOutlined, RobotOutlined, UploadOutlined } from "@ant-design/icons";
import {
  extractFromText,
  recognizeImageFile,
  generatePageCopy,
  getAIErrorMessage,
  type PageCopyResult,
  type ExtractedFields,
} from "@/lib/ai";
import type { FormInstance } from "antd";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

interface AIDrawerProps {
  open: boolean;
  onClose: () => void;
  form: FormInstance;
}

export function AIDrawer({ open, onClose, form }: AIDrawerProps) {
  const { message } = App.useApp();
  const [recognizing, setRecognizing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [extractedFields, setExtractedFields] = useState<ExtractedFields | null>(null);
  const [pageCopy, setPageCopy] = useState<PageCopyResult["result"] | null>(null);
  const [textInput, setTextInput] = useState("");

  const reset = () => { setExtractedFields(null); setPageCopy(null); setTextInput(""); };

  const handleImageRecognize = async (file: File) => {
    setRecognizing(true);
    try {
      const result = await recognizeImageFile(file);
      setExtractedFields(result.fields);
      message.success("AI 识别完成，请查看识别结果");
    } catch (err) { message.error(getAIErrorMessage(err)); }
    finally { setRecognizing(false); }
    return false;
  };

  const handleTextExtract = async () => {
    if (!textInput.trim()) { message.warning("请输入产品描述文本"); return; }
    setRecognizing(true);
    try {
      const result = await extractFromText(textInput);
      setExtractedFields(result.fields);
      message.success("AI 提取完成");
    } catch (err) { message.error(getAIErrorMessage(err)); }
    finally { setRecognizing(false); }
  };

  const handleGeneratePageCopy = async () => {
    if (!extractedFields?.product_name) { message.warning("请先识别产品信息"); return; }
    setGenerating(true);
    try {
      const result = await generatePageCopy(extractedFields.product_name, extractedFields.category || "其他", extractedFields.origin ? [extractedFields.origin] : []);
      setPageCopy(result.result);
      message.success("页面文案生成完成");
    } catch (err) { message.error(getAIErrorMessage(err)); }
    finally { setGenerating(false); }
  };

  const applyFieldsToForm = () => {
    if (!extractedFields) return;
    const updates: Record<string, string> = {};
    if (extractedFields.product_name) updates.name = extractedFields.product_name;
    if (extractedFields.category) updates.category = extractedFields.category;
    if (extractedFields.origin) {
      updates.description = `产地：${extractedFields.origin}`;
      if (extractedFields.weight) updates.description += `；重量：${extractedFields.weight}`;
      if (extractedFields.shelf_life) updates.description += `；保质期：${extractedFields.shelf_life}`;
      updates.origin = extractedFields.origin;
    }
    form.setFieldsValue(updates);
    message.success("已自动填充产品资料");
    onClose();
  };

  return (
    <Drawer title={<span><RobotOutlined /> AI 智能识别</span>} open={open} onClose={() => { reset(); onClose(); }} size="large">
      <div className="space-y-6">
        <div>
          <Title level={5}><CameraOutlined /> 上传产品图片识别</Title>
          <Upload accept="image/jpeg,image/png,image/webp" showUploadList={false} beforeUpload={(file) => { handleImageRecognize(file); return false; }}>
            <Button icon={<UploadOutlined />} loading={recognizing}>选择产品图片</Button>
          </Upload>
          <Text type="secondary" className="ml-2">支持 JPG、PNG、WebP 格式</Text>
        </div>
        <Divider>或</Divider>
        <div>
          <Title level={5}><FileTextOutlined /> 粘贴产品描述文本</Title>
          <TextArea rows={4} value={textInput} onChange={(e) => setTextInput(e.target.value)} placeholder="例如：赣南脐橙，产地江西赣州，净重5kg，保质期6个月" />
          <Button className="mt-2" type="primary" onClick={handleTextExtract} loading={recognizing} icon={<RobotOutlined />}>AI 提取产品信息</Button>
        </div>
        {extractedFields && (
          <>
            <Divider>识别结果</Divider>
            <div>
              <Alert type="success" title="AI 已识别以下产品信息" className="mb-3" />
              <div className="rounded border p-4 space-y-2">
                {extractedFields.product_name && <div><Text strong>产品名称：</Text>{extractedFields.product_name}</div>}
                {extractedFields.category && <div><Text strong>品类：</Text>{extractedFields.category}</div>}
                {extractedFields.origin && <div><Text strong>产地：</Text>{extractedFields.origin}</div>}
                {extractedFields.weight && <div><Text strong>重量：</Text>{extractedFields.weight}</div>}
                {extractedFields.shelf_life && <div><Text strong>保质期：</Text>{extractedFields.shelf_life}</div>}
              </div>
              <Button className="mt-3" type="primary" onClick={applyFieldsToForm} icon={<PlusOutlined />}>自动填充到新建产品表单</Button>
            </div>
            <Divider><BulbOutlined /> AI 页面文案</Divider>
            <Button onClick={handleGeneratePageCopy} loading={generating} icon={<RobotOutlined />}>生成页面文案和推荐模板</Button>
            {pageCopy && (
              <div className="space-y-4 mt-4">
                <div><Text strong>品牌故事</Text><Paragraph className="mt-1 rounded bg-gray-50 p-3">{pageCopy.copywriting.brand_story}</Paragraph></div>
                <div><Text strong>核心卖点</Text>
                  <List className="mt-1" size="small" dataSource={pageCopy.copywriting.selling_points}
                    renderItem={(item) => <List.Item><Text strong>{item.title}</Text><Text className="ml-2">{item.detail}</Text></List.Item>} />
                </div>
                <div><Text strong>推荐模板：</Text><Tag color="blue" className="ml-2">{pageCopy.recommended_template.name}</Tag></div>
                <div><Text strong>推荐页面模块：</Text>
                  <div className="mt-1 flex flex-wrap gap-1">{pageCopy.page_suggestion.modules.map((mod) => <Tag key={mod.id}>{mod.type}</Tag>)}</div>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </Drawer>
  );
}
