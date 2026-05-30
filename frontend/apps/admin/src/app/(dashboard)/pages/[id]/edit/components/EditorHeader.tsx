"use client";

import { useRouter } from "next/navigation";
import { App, Button, Popconfirm, Space, Tag } from "antd";
import {
  ArrowLeftOutlined,
  SaveOutlined,
  SendOutlined,
} from "@ant-design/icons";
import api from "@/lib/api";

interface EditorHeaderProps {
  templateId: string;
  templateName: string;
  version: number;
  versionStatus: string;
  versionId: string;
  saving: boolean;
  onSave: () => void;
  onRefresh: () => void;
}

export function EditorHeader({
  templateId,
  templateName,
  version,
  versionStatus,
  versionId,
  saving,
  onSave,
  onRefresh,
}: EditorHeaderProps) {
  const router = useRouter();
  const { message } = App.useApp();

  const handlePublish = async () => {
    try {
      await api.post(`/page-versions/${versionId}/publish`);
      message.success("发布成功");
      onRefresh();
    } catch {
      message.error("发布失败");
    }
  };

  return (
    <div className="flex h-12 items-center justify-between border-b bg-white px-4">
      <div className="flex items-center gap-3">
        <Button
          type="text"
          icon={<ArrowLeftOutlined />}
          onClick={() => router.push(`/pages/${templateId}`)}
        >
          返回
        </Button>
        <span className="font-medium">{templateName}</span>
        <Tag color={versionStatus === "draft" ? "default" : "blue"}>
          草稿 v{version}
        </Tag>
      </div>
      <Space>
        <Button icon={<SaveOutlined />} onClick={onSave} loading={saving}>
          保存
        </Button>
        {versionStatus === "draft" && (
          <Popconfirm title="确认发布此版本？" onConfirm={handlePublish}>
            <Button type="primary" icon={<SendOutlined />}>
              发布
            </Button>
          </Popconfirm>
        )}
      </Space>
    </div>
  );
}