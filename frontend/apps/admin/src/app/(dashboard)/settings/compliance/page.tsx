"use client";

import { useEffect, useState } from "react";
import DOMPurify from "dompurify";
import {
  App,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Result,
  Space,
  Spin,
  Switch,
  Tabs,
  Typography,
} from "antd";
import { EyeOutlined, SaveOutlined } from "@ant-design/icons";
import api from "@/lib/api";

const { Title } = Typography;
const { TextArea } = Input;

const PRIVACY_POLICY_ALLOWED_TAGS = [
  "div",
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "span",
  "a",
  "img",
  "ul",
  "ol",
  "li",
  "br",
  "hr",
  "strong",
  "em",
  "b",
  "i",
  "u",
  "s",
  "sub",
  "sup",
  "table",
  "thead",
  "tbody",
  "tr",
  "td",
  "th",
  "caption",
  "blockquote",
  "pre",
  "code",
  "figure",
  "figcaption",
  "section",
  "article",
  "aside",
  "header",
  "footer",
  "nav",
  "video",
  "audio",
  "source",
  "picture",
];

const PRIVACY_POLICY_ALLOWED_ATTRS = [
  "href",
  "src",
  "alt",
  "class",
  "style",
  "id",
  "target",
  "rel",
  "width",
  "height",
  "loading",
  "controls",
  "playsinline",
  "muted",
  "preload",
  "poster",
  "colspan",
  "rowspan",
  "scope",
];

function sanitizePrivacyPolicyHtml(content: string) {
  return DOMPurify.sanitize(content, {
    ALLOWED_TAGS: PRIVACY_POLICY_ALLOWED_TAGS,
    ALLOWED_ATTR: PRIVACY_POLICY_ALLOWED_ATTRS,
    ALLOW_DATA_ATTR: false,
    FORBID_TAGS: [
      "script",
      "iframe",
      "object",
      "embed",
      "form",
      "input",
      "style",
      "link",
      "base",
      "meta",
    ],
    FORBID_ATTR: [
      "onerror",
      "onload",
      "onclick",
      "onmouseover",
      "onfocus",
      "onblur",
      "onsubmit",
      "onchange",
      "oninput",
      "onkeydown",
      "onkeyup",
      "onkeypress",
      "onmousedown",
      "onmouseup",
      "ondblclick",
      "oncontextmenu",
      "onwheel",
      "ondrag",
      "ondrop",
      "oncopy",
      "oncut",
      "onpaste",
      "ontouchstart",
      "ontouchend",
      "ontouchmove",
    ],
  });
}

export default function CompliancePage() {
  const { message } = App.useApp();
  const [privacyForm] = Form.useForm();
  const [retentionForm] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewPolicy, setPreviewPolicy] = useState({
    version: "",
    content: "",
  });

  const [phoneAuth, setPhoneAuth] = useState(true);
  const [locationAuth, setLocationAuth] = useState(false);
  const [wechatAuth, setWechatAuth] = useState(true);

  // Load existing settings from tenant config
  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const { data: tenant } = await api.get("/tenants/me");
        const compliance = tenant.compliance_settings as
          Record<string, unknown> | undefined;
        if (compliance) {
          privacyForm.setFieldsValue({
            version: (compliance.privacy_version as string) || "1.0",
            content: (compliance.privacy_content as string) || "",
          });
          retentionForm.setFieldsValue({
            retention_days: (compliance.retention_days as number) || 365,
            auto_cleanup: compliance.auto_cleanup !== false,
          });
          setPhoneAuth(compliance.phone_auth !== false);
          setLocationAuth(compliance.location_auth === true);
          setWechatAuth(compliance.wechat_auth !== false);
        }
      } catch {
        message.error("加载合规设置失败，请刷新页面重试");
        setLoadError(true);
      } finally {
        setLoading(false);
      }
    };
    fetchSettings();
  }, [message, privacyForm, retentionForm]);

  const handleSavePrivacy = async () => {
    setSaving(true);
    try {
      const values = await privacyForm.validateFields();
      await api.patch("/tenants/me", {
        compliance_settings: {
          privacy_version: values.version,
          privacy_content: values.content,
        },
      });
      message.success("隐私政策已保存");
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      if (err.response?.data?.detail) message.error(err.response.data.detail);
    } finally {
      setSaving(false);
    }
  };

  const handlePreviewPrivacy = () => {
    const values = privacyForm.getFieldsValue(["version", "content"]);
    setPreviewPolicy({
      version: String(values.version || "").trim(),
      content: String(values.content || ""),
    });
    setPreviewOpen(true);
  };

  const handleSaveAuthorization = async () => {
    setSaving(true);
    try {
      await api.patch("/tenants/me", {
        compliance_settings: {
          phone_auth: phoneAuth,
          location_auth: locationAuth,
          wechat_auth: wechatAuth,
        },
      });
      message.success("授权设置已保存");
    } catch {
      message.error("保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleSaveRetention = async () => {
    setSaving(true);
    try {
      const values = await retentionForm.validateFields();
      await api.patch("/tenants/me", {
        compliance_settings: {
          retention_days: values.retention_days,
          auto_cleanup: values.auto_cleanup,
        },
      });
      message.success("数据保留设置已保存");
    } catch {
      message.error("保存失败");
    } finally {
      setSaving(false);
    }
  };

  const tabItems = [
    {
      key: "privacy",
      label: "隐私政策",
      children: (
        <Form
          form={privacyForm}
          layout="vertical"
          onFinish={handleSavePrivacy}
          className="max-w-3xl"
        >
          <Form.Item
            name="version"
            label="版本号"
            rules={[{ required: true, message: "请输入版本号" }]}
          >
            <Input placeholder="例如 1.0" style={{ width: 200 }} />
          </Form.Item>
          <Form.Item
            name="content"
            label="隐私政策内容"
            rules={[{ required: true, message: "请输入隐私政策内容" }]}
          >
            <TextArea rows={16} placeholder="请输入隐私政策内容" />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button
                type="primary"
                htmlType="submit"
                icon={<SaveOutlined />}
                loading={saving}
              >
                保存
              </Button>
              <Button icon={<EyeOutlined />} onClick={handlePreviewPrivacy}>
                预览消费者效果
              </Button>
            </Space>
          </Form.Item>
        </Form>
      ),
    },
    {
      key: "authorization",
      label: "授权管理",
      children: (
        <div className="max-w-3xl">
          <div className="mb-6 space-y-6">
            <div className="flex items-center justify-between p-4 border rounded">
              <div>
                <div className="font-medium">手机号授权</div>
                <div className="text-text-muted text-sm">
                  消费者扫码时是否需要授权手机号
                </div>
              </div>
              <Switch
                checked={phoneAuth}
                onChange={setPhoneAuth}
                checkedChildren="开启"
                unCheckedChildren="关闭"
              />
            </div>
            <div className="flex items-center justify-between p-4 border rounded">
              <div>
                <div className="font-medium">位置授权</div>
                <div className="text-text-muted text-sm">
                  消费者扫码时是否需要授权地理位置
                </div>
              </div>
              <Switch
                checked={locationAuth}
                onChange={setLocationAuth}
                checkedChildren="开启"
                unCheckedChildren="关闭"
              />
            </div>
            <div className="flex items-center justify-between p-4 border rounded">
              <div>
                <div className="font-medium">微信授权</div>
                <div className="text-text-muted text-sm">
                  消费者扫码时是否需要微信授权（获取 openid）
                </div>
              </div>
              <Switch
                checked={wechatAuth}
                onChange={setWechatAuth}
                checkedChildren="开启"
                unCheckedChildren="关闭"
              />
            </div>
          </div>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            onClick={handleSaveAuthorization}
            loading={saving}
          >
            保存授权设置
          </Button>
        </div>
      ),
    },
    {
      key: "retention",
      label: "数据保留",
      children: (
        <Form
          form={retentionForm}
          layout="vertical"
          onFinish={handleSaveRetention}
          className="max-w-3xl"
        >
          <Form.Item
            name="retention_days"
            label="数据保留天数"
            rules={[{ required: true }]}
            extra="超过保留期限的数据将自动清理"
          >
            <InputNumber
              min={30}
              max={3650}
              placeholder="保留天数"
              addonAfter="天"
              style={{ width: 200 }}
            />
          </Form.Item>
          <Form.Item
            name="auto_cleanup"
            label="自动清理"
            valuePropName="checked"
            extra="开启后系统将按设定天数自动清理过期数据"
          >
            <Switch checkedChildren="开启" unCheckedChildren="关闭" />
          </Form.Item>
          <Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              icon={<SaveOutlined />}
              loading={saving}
            >
              保存
            </Button>
          </Form.Item>
        </Form>
      ),
    },
  ];

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Spin size="large" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div>
        <Title level={4}>合规设置</Title>
        <Result
          status="error"
          title="加载失败"
          subTitle="无法加载合规设置，请刷新页面重试"
          extra={
            <Button type="primary" onClick={() => window.location.reload()}>
              刷新页面
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div>
      <Title level={4}>合规设置</Title>
      <Tabs defaultActiveKey="privacy" items={tabItems} />
      <Modal
        open={previewOpen}
        title="消费者端预览"
        width={430}
        onCancel={() => setPreviewOpen(false)}
        footer={<Button onClick={() => setPreviewOpen(false)}>关闭</Button>}
      >
        <div className="mx-auto max-w-98 rounded-2xl bg-bg-muted p-3">
          <section className="rounded-2xl bg-bg-container p-4 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <h3
                className="m-0 text-base font-semibold"
                style={{ color: "var(--ymt-color-text-primary)" }}
              >
                隐私政策
              </h3>
              {previewPolicy.version && (
                <span
                  className="shrink-0 rounded-full px-2 py-1 text-xs"
                  style={{
                    background: "var(--ymt-color-feedback-info-bg)",
                    color: "var(--ymt-color-feedback-info)",
                  }}
                >
                  版本 {previewPolicy.version}
                </span>
              )}
            </div>
            {previewPolicy.content.trim() ? (
              <div className="mt-3 max-h-[60vh] overflow-y-auto">
                {previewPolicy.content.trim().startsWith("<") ? (
                  <div
                    className="prose prose-sm max-w-none text-sm [&_a]:underline"
                    style={{ color: "var(--ymt-color-text-secondary)" }}
                    dangerouslySetInnerHTML={{
                      __html: sanitizePrivacyPolicyHtml(previewPolicy.content),
                    }}
                  />
                ) : (
                  <p
                    className="m-0 whitespace-pre-wrap text-sm leading-relaxed"
                    style={{ color: "var(--ymt-color-text-secondary)" }}
                  >
                    {previewPolicy.content}
                  </p>
                )}
              </div>
            ) : (
              <div
                className="mt-3 rounded-lg bg-bg-muted px-4 py-8 text-center text-sm"
                style={{ color: "var(--ymt-color-text-tertiary)" }}
              >
                暂无可预览内容，请先填写隐私政策。
              </div>
            )}
          </section>
        </div>
      </Modal>
    </div>
  );
}
