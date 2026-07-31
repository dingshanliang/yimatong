"use client";

import { useRouter } from "next/navigation";
import { Button, Space, Tag, Typography } from "antd";
import {
  ArrowLeftOutlined,
  SaveOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { STATUS_COLORS } from "@/lib/status-colors";

const { Text } = Typography;

interface EditorHeaderProps {
  templateId: string;
  templateName: string;
  version: number;
  versionStatus: string;
  productName?: string | null;
  moduleCount: number;
  issueCount: number;
  saving: boolean;
  onSave: () => void;
  onPublish: () => void;
}

export function EditorHeader({
  templateId,
  templateName,
  version,
  versionStatus,
  productName,
  moduleCount,
  issueCount,
  saving,
  onSave,
  onPublish,
}: EditorHeaderProps) {
  const router = useRouter();

  return (
    <div className="flex h-12 items-center justify-between border-b bg-bg-container px-4">
      <div className="flex min-w-0 items-center gap-3">
        <Button
          type="text"
          icon={<ArrowLeftOutlined />}
          onClick={() => router.push(`/pages/${templateId}`)}
        >
          返回
        </Button>
        <span className="truncate font-medium">{templateName}</span>
        <Tag
          color={
            versionStatus === "draft"
              ? STATUS_COLORS.neutral
              : STATUS_COLORS.processing
          }
        >
          {versionStatus === "draft" ? "草稿" : "已发布"} v{version}
        </Tag>
        <Text type="secondary" className="hidden text-xs lg:inline">
          {productName ? `关联产品：${productName}` : "未关联产品"}
        </Text>
        <Tag>{moduleCount} 个模块</Tag>
        {issueCount > 0 ? (
          <Tag color={STATUS_COLORS.warning}>{issueCount} 项待确认</Tag>
        ) : (
          <Tag color={STATUS_COLORS.success}>可发布检查通过</Tag>
        )}
      </div>
      <Space>
        {versionStatus === "draft" && (
          <>
            <Button icon={<SaveOutlined />} onClick={onSave} loading={saving}>
              保存草稿
            </Button>
            <Button type="primary" icon={<SendOutlined />} onClick={onPublish}>
              发布草稿
            </Button>
          </>
        )}
      </Space>
    </div>
  );
}
