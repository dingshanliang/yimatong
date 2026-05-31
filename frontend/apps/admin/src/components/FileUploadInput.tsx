"use client";

import { useState } from "react";
import { App, Button, Input, Space, Typography, Upload } from "antd";
import type { InputProps } from "antd";
import { DeleteOutlined, LinkOutlined, UploadOutlined } from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";

const { Link, Text } = Typography;

interface UploadResponse {
  public_url?: string;
  file_url?: string;
  filename?: string;
}

interface FileUploadInputProps {
  value?: string;
  onChange?: (value?: string) => void;
  module?: string;
  accept?: string;
  buttonText?: string;
  placeholder?: string;
  emptyText?: string;
  size?: InputProps["size"];
  variant?: "compact" | "uploadFirst";
}

export default function FileUploadInput({
  value,
  onChange,
  module = "files",
  accept = "application/pdf,image/jpeg,image/png,image/webp",
  buttonText = "上传文件",
  placeholder = "可直接上传，或粘贴公开可访问的文件链接",
  emptyText = "支持 PDF、PNG、JPG、WebP，单个文件不超过 20MB。",
  size,
  variant = "compact",
}: FileUploadInputProps) {
  const { message } = App.useApp();
  const [uploading, setUploading] = useState(false);
  const [showUrlInput, setShowUrlInput] = useState(false);

  const handleUpload = async (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("module", module);

    setUploading(true);
    try {
      const { data } = await api.post<UploadResponse>("/files/upload", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      const fileUrl = data.public_url || data.file_url;
      onChange?.(fileUrl);
      message.success("文件上传成功");
    } catch (err) {
      message.error(extractErrorMessage(err, "文件上传失败"));
    } finally {
      setUploading(false);
    }
  };

  const uploadButton = (
    <Upload
      accept={accept}
      showUploadList={false}
      beforeUpload={(file) => {
        void handleUpload(file);
        return Upload.LIST_IGNORE;
      }}
    >
      <Button size={size} icon={<UploadOutlined />} loading={uploading}>
        {buttonText}
      </Button>
    </Upload>
  );

  if (variant === "uploadFirst") {
    return (
      <Space orientation="vertical" size={10} className="w-full">
        <Space wrap>
          {uploadButton}
          <Button
            type="link"
            className="!px-0"
            size={size || "small"}
            icon={<LinkOutlined />}
            onClick={() => setShowUrlInput((prev) => !prev)}
          >
            {showUrlInput ? "收起文件链接" : "粘贴文件链接"}
          </Button>
          {value && (
            <Button
              type="text"
              danger
              size={size || "small"}
              icon={<DeleteOutlined />}
              onClick={() => onChange?.(undefined)}
            >
              清除
            </Button>
          )}
        </Space>
        {value ? (
          <Link href={value} target="_blank" rel="noreferrer">
            查看已上传文件
          </Link>
        ) : (
          <Text type="secondary">{emptyText}</Text>
        )}
        {showUrlInput && (
          <Input
            size={size}
            value={value}
            onChange={(event) => onChange?.(event.target.value || undefined)}
            placeholder={placeholder}
            allowClear
          />
        )}
      </Space>
    );
  }

  return (
    <Space orientation="vertical" size={8} className="w-full">
      <Space.Compact className="w-full">
        <Input
          size={size}
          value={value}
          onChange={(event) => onChange?.(event.target.value || undefined)}
          placeholder={placeholder}
          allowClear
        />
        {uploadButton}
      </Space.Compact>
      {value ? (
        <Link href={value} target="_blank" rel="noreferrer">
          查看已上传文件
        </Link>
      ) : (
        <Text type="secondary">{emptyText}</Text>
      )}
    </Space>
  );
}
