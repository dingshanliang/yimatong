"use client";

import { useEffect, useState, useRef } from "react";
import {
  Table,
  Button,
  Space,
  Input,
  Modal,
  Form,
  Select,
  message,
  Tag,
  Typography,
  Upload,
  Drawer,
  Spin,
  Divider,
  Alert,
  List,
} from "antd";
import {
  PlusOutlined,
  SearchOutlined,
  CameraOutlined,
  RobotOutlined,
  UploadOutlined,
  FileTextOutlined,
  BulbOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { usePaginatedList } from "@/lib/hooks";
import {
  extractFromText,
  recognizeImage,
  generatePageCopy,
  type PageCopyResult,
  type ExtractedFields,
} from "@/lib/ai";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

interface Product {
  id: string;
  name: string;
  brand_id: string;
  brand_name?: string;
  category?: string;
  status: string;
  created_at: string;
}

interface Brand {
  id: string;
  name: string;
}

export default function ProductsPage() {
  const [brands, setBrands] = useState<Brand[]>([]);
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Product | null>(null);
  const [form] = Form.useForm();

  // AI 相关状态
  const [aiDrawerOpen, setAiDrawerOpen] = useState(false);
  const [aiRecognizing, setAiRecognizing] = useState(false);
  const [aiGenerating, setAiGenerating] = useState(false);
  const [aiExtractedFields, setAiExtractedFields] = useState<ExtractedFields | null>(null);
  const [aiPageCopy, setAiPageCopy] = useState<PageCopyResult | null>(null);
  const [aiTextInput, setAiTextInput] = useState("");

  const { items: products, total, page, loading, setPage, refresh } = usePaginatedList<Product>(
    async ({ page, page_size }) => {
      try {
        const params: Record<string, string | number> = { page, page_size };
        if (search) params.search = search;
        const { data } = await api.get("/products", { params });
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        message.error("加载产品列表失败");
        return { items: [], total: 0 };
      }
    },
    [search]
  );

  const fetchBrands = async () => {
    try {
      const { data } = await api.get("/brands", { params: { page_size: 100 } });
      setBrands(data.items || []);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    fetchBrands();
  }, []);

  const openCreate = () => {
    setEditItem(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (product: Product) => {
    setEditItem(product);
    form.setFieldsValue(product);
    setModalOpen(true);
  };

  const handleCreate = async (values: Record<string, string>) => {
    try {
      if (editItem) {
        await api.patch(`/products/${editItem.id}`, values);
        message.success("产品更新成功");
      } else {
        await api.post("/products", values);
        message.success("产品创建成功");
      }
      setModalOpen(false);
      form.resetFields();
      setPage(1);
      refresh();
    } catch {
      message.error(editItem ? "更新失败" : "创建失败");
    }
  };

  // --- AI 功能 ---

  const handleImageRecognize = async (file: File) => {
    setAiRecognizing(true);
    try {
      const result = await recognizeImage(file);
      setAiExtractedFields(result.fields);
      message.success("AI 识别完成，请查看识别结果");
    } catch {
      message.error("AI 识别失败，请重试");
    } finally {
      setAiRecognizing(false);
    }
    return false; // 阻止 Upload 组件自动上传
  };

  const handleTextExtract = async () => {
    if (!aiTextInput.trim()) {
      message.warning("请输入产品描述文本");
      return;
    }
    setAiRecognizing(true);
    try {
      const result = await extractFromText(aiTextInput);
      setAiExtractedFields(result.fields);
      message.success("AI 提取完成");
    } catch {
      message.error("AI 提取失败");
    } finally {
      setAiRecognizing(false);
    }
  };

  const handleGeneratePageCopy = async () => {
    if (!aiExtractedFields?.product_name) {
      message.warning("请先识别产品信息");
      return;
    }
    setAiGenerating(true);
    try {
      const result = await generatePageCopy(
        aiExtractedFields.product_name,
        aiExtractedFields.category || "其他",
        aiExtractedFields.origin ? [aiExtractedFields.origin] : [],
      );
      setAiPageCopy(result);
      message.success("页面文案生成完成");
    } catch {
      message.error("文案生成失败");
    } finally {
      setAiGenerating(false);
    }
  };

  const applyFieldsToForm = () => {
    if (!aiExtractedFields) return;
    const updates: Record<string, string> = {};
    if (aiExtractedFields.product_name) updates.name = aiExtractedFields.product_name;
    if (aiExtractedFields.category) updates.category = aiExtractedFields.category;
    if (aiExtractedFields.origin) {
      updates.description = `产地：${aiExtractedFields.origin}`;
      if (aiExtractedFields.weight) updates.description += `；重量：${aiExtractedFields.weight}`;
      if (aiExtractedFields.shelf_life) updates.description += `；保质期：${aiExtractedFields.shelf_life}`;
    }
    form.setFieldsValue(updates);
    message.success("已自动填充产品资料");
    setAiDrawerOpen(false);
  };

  const columns: ColumnsType<Product> = [
    { title: "产品名称", dataIndex: "name", key: "name" },
    { title: "品牌", dataIndex: "brand_name", key: "brand_name" },
    { title: "品类", dataIndex: "category", key: "category" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: string) => (
        <Tag color={status === "active" ? "green" : "default"}>
          {status === "active" ? "启用" : status}
        </Tag>
      ),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => new Date(v).toLocaleDateString("zh-CN"),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Product) => (
        <Button type="link" size="small" onClick={() => openEdit(record)}>编辑</Button>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          产品管理
        </Title>
        <Space>
          <Input
            placeholder="搜索产品名称"
            prefix={<SearchOutlined />}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            allowClear
          />
          <Button
            icon={<RobotOutlined />}
            onClick={() => {
              setAiDrawerOpen(true);
              setAiExtractedFields(null);
              setAiPageCopy(null);
              setAiTextInput("");
            }}
          >
            AI 智能识别
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={openCreate}
          >
            新建产品
          </Button>
        </Space>
      </div>
      <Table
        columns={columns}
        dataSource={products}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />
      <Modal
        title={editItem ? "编辑产品" : "新建产品"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="name"
            label="产品名称"
            rules={[{ required: true, message: "请输入产品名称" }]}
          >
            <Input data-testid="product-name-input" />
          </Form.Item>
          <Form.Item
            name="brand_id"
            label="品牌"
            rules={[{ required: true, message: "请选择品牌" }]}
          >
            <Select
              placeholder="选择品牌"
              options={brands.map((b) => ({ value: b.id, label: b.name }))}
              data-testid="product-brand-select"
            />
          </Form.Item>
          <Form.Item name="category" label="品类">
            <Input data-testid="product-category-input" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} data-testid="product-description-input" />
          </Form.Item>
        </Form>
      </Modal>

      {/* AI 智能识别 Drawer */}
      <Drawer
        title={
          <Space>
            <RobotOutlined />
            <span>AI 智能识别</span>
          </Space>
        }
        open={aiDrawerOpen}
        onClose={() => setAiDrawerOpen(false)}
        width={640}
      >
        <div className="space-y-6">
          {/* 方式一：上传图片 */}
          <div>
            <Title level={5}>
              <CameraOutlined /> 上传产品图片识别
            </Title>
            <Upload
              accept="image/jpeg,image/png,image/webp"
              showUploadList={false}
              beforeUpload={(file) => {
                handleImageRecognize(file);
                return false;
              }}
            >
              <Button icon={<UploadOutlined />} loading={aiRecognizing}>
                选择产品图片
              </Button>
            </Upload>
            <Text type="secondary" className="ml-2">
              支持 JPG、PNG、WebP 格式
            </Text>
          </div>

          <Divider>或</Divider>

          {/* 方式二：粘贴文本 */}
          <div>
            <Title level={5}>
              <FileTextOutlined /> 粘贴产品描述文本
            </Title>
            <TextArea
              rows={4}
              value={aiTextInput}
              onChange={(e) => setAiTextInput(e.target.value)}
              placeholder="例如：赣南脐橙，产地江西赣州，净重5kg，保质期6个月"
            />
            <Button
              className="mt-2"
              type="primary"
              onClick={handleTextExtract}
              loading={aiRecognizing}
              icon={<RobotOutlined />}
            >
              AI 提取产品信息
            </Button>
          </div>

          {/* 识别结果 */}
          {aiExtractedFields && (
            <>
              <Divider>识别结果</Divider>
              <div>
                <Alert
                  type="success"
                  message="AI 已识别以下产品信息"
                  className="mb-3"
                />
                <div className="rounded border p-4 space-y-2">
                  {aiExtractedFields.product_name && (
                    <div><Text strong>产品名称：</Text>{aiExtractedFields.product_name}</div>
                  )}
                  {aiExtractedFields.category && (
                    <div><Text strong>品类：</Text>{aiExtractedFields.category}</div>
                  )}
                  {aiExtractedFields.origin && (
                    <div><Text strong>产地：</Text>{aiExtractedFields.origin}</div>
                  )}
                  {aiExtractedFields.weight && (
                    <div><Text strong>重量：</Text>{aiExtractedFields.weight}</div>
                  )}
                  {aiExtractedFields.shelf_life && (
                    <div><Text strong>保质期：</Text>{aiExtractedFields.shelf_life}</div>
                  )}
                </div>
                <Button
                  className="mt-3"
                  type="primary"
                  onClick={applyFieldsToForm}
                  icon={<PlusOutlined />}
                >
                  自动填充到新建产品表单
                </Button>
              </div>

              {/* 生成页面文案 */}
              <Divider>
                <BulbOutlined /> AI 页面文案
              </Divider>
              <Button
                onClick={handleGeneratePageCopy}
                loading={aiGenerating}
                icon={<RobotOutlined />}
              >
                生成页面文案和推荐模板
              </Button>

              {aiPageCopy && (
                <div className="space-y-4 mt-4">
                  <div>
                    <Text strong>品牌故事</Text>
                    <Paragraph className="mt-1 rounded bg-gray-50 p-3">
                      {aiPageCopy.copywriting.brand_story}
                    </Paragraph>
                  </div>
                  <div>
                    <Text strong>核心卖点</Text>
                    <List
                      className="mt-1"
                      size="small"
                      dataSource={aiPageCopy.copywriting.selling_points.items}
                      renderItem={(item) => (
                        <List.Item>
                          <Text>{item}</Text>
                        </List.Item>
                      )}
                    />
                  </div>
                  <div>
                    <Text strong>推荐模板：</Text>
                    <Tag color="blue" className="ml-2">
                      {aiPageCopy.recommended_template.name}
                    </Tag>
                  </div>
                  <div>
                    <Text strong>推荐页面模块：</Text>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {aiPageCopy.page_suggestion.modules.map((mod) => (
                        <Tag key={mod}>{mod}</Tag>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </Drawer>
    </div>
  );
}
