"use client";

import { useState } from "react";
import {
  Tabs,
  Form,
  Input,
  InputNumber,
  Switch,
  Button,
  Typography,
  Space,
  message,
} from "antd";
import { SaveOutlined } from "@ant-design/icons";

const { Title } = Typography;
const { TextArea } = Input;

export default function CompliancePage() {
  const [privacyForm] = Form.useForm();
  const [retentionForm] = Form.useForm();
  const [saving, setSaving] = useState(false);

  // Authorization toggles
  const [phoneAuth, setPhoneAuth] = useState(true);
  const [locationAuth, setLocationAuth] = useState(false);
  const [wechatAuth, setWechatAuth] = useState(true);

  const handleSavePrivacy = async () => {
    setSaving(true);
    try {
      // TODO: call API to save privacy policy
      await new Promise((resolve) => setTimeout(resolve, 500));
      message.success("隐私政策已保存");
    } catch {
      message.error("保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAuthorization = async () => {
    setSaving(true);
    try {
      // TODO: call API to save authorization settings
      await new Promise((resolve) => setTimeout(resolve, 500));
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
      // TODO: call API to save retention settings
      await new Promise((resolve) => setTimeout(resolve, 500));
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
          initialValues={{
            version: "1.0",
            content:
              "一码通隐私政策\n\n本应用重视您的隐私保护。在您使用本应用提供的服务时，本应用将按照本隐私政策处理您的个人信息。\n\n1. 信息收集\n我们仅收集提供服务所必需的最少信息...",
          }}
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
    {
      key: "authorization",
      label: "授权管理",
      children: (
        <div className="max-w-3xl">
          <div className="mb-6 space-y-6">
            <div className="flex items-center justify-between p-4 border rounded">
              <div>
                <div className="font-medium">手机号授权</div>
                <div className="text-gray-400 text-sm">
                  消费者扫码时是否需要授权手机号
                </div>
              </div>
              <Switch
                checked={phoneAuth}
                onChange={(v) => setPhoneAuth(v)}
                checkedChildren="开启"
                unCheckedChildren="关闭"
              />
            </div>

            <div className="flex items-center justify-between p-4 border rounded">
              <div>
                <div className="font-medium">位置授权</div>
                <div className="text-gray-400 text-sm">
                  消费者扫码时是否需要授权地理位置
                </div>
              </div>
              <Switch
                checked={locationAuth}
                onChange={(v) => setLocationAuth(v)}
                checkedChildren="开启"
                unCheckedChildren="关闭"
              />
            </div>

            <div className="flex items-center justify-between p-4 border rounded">
              <div>
                <div className="font-medium">微信授权</div>
                <div className="text-gray-400 text-sm">
                  消费者扫码时是否需要微信授权（获取 openid）
                </div>
              </div>
              <Switch
                checked={wechatAuth}
                onChange={(v) => setWechatAuth(v)}
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
          initialValues={{
            retention_days: 365,
            auto_cleanup: true,
          }}
          onFinish={handleSaveRetention}
          className="max-w-3xl"
        >
          <Form.Item
            name="retention_days"
            label="数据保留天数"
            rules={[{ required: true, message: "请输入保留天数" }]}
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
            <Space>
              <Button
                type="primary"
                htmlType="submit"
                icon={<SaveOutlined />}
                loading={saving}
              >
                保存
              </Button>
            </Space>
          </Form.Item>
        </Form>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>合规设置</Title>
      <Tabs defaultActiveKey="privacy" items={tabItems} />
    </div>
  );
}
