"use client";

import { useState } from "react";
import { App, Button, Image, Input, Space, Typography, Upload } from "antd";
import type { InputProps } from "antd";
import { LinkOutlined, UploadOutlined } from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";

const { Text } = Typography;

interface UploadResponse {
  public_url?: string;
  file_url?: string;
}

interface ImageUploadInputProps {
  value?: string;
  onChange?: (value?: string) => void;
  module?: string;
  buttonText?: string;
  placeholder?: string;
  emptyText?: string;
  previewAlt?: string;
  size?: InputProps["size"];
  variant?: "compact" | "uploadFirst";
}

export default function ImageUploadInput({
  value,
  onChange,
  module = "images",
  buttonText = "上传图片",
  placeholder = "可直接上传，或粘贴 https://cdn.example.com/image.png",
  emptyText = "支持 PNG、JPG、WebP，单张不超过 5MB；也可直接粘贴已有图片链接。",
  previewAlt = "图片预览",
  size,
  variant = "compact",
}: ImageUploadInputProps) {
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
      const imageUrl = data.public_url || data.file_url;
      onChange?.(imageUrl);
      message.success("图片上传成功");
    } catch (err) {
      message.error(extractErrorMessage(err, "图片上传失败"));
    } finally {
      setUploading(false);
    }
  };

  const uploadButton = (
    <Upload
      accept="image/jpeg,image/png,image/webp"
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
        <div className="flex flex-wrap items-center gap-3">
          <div className="admin-image-placeholder flex h-24 w-24 items-center justify-center rounded border border-dashed">
            {value ? (
              <Image src={value} alt={previewAlt} width={88} height={88} className="rounded object-contain" />
            ) : (
              <Text type="secondary" className="text-xs">暂无图片</Text>
            )}
          </div>
          <Space orientation="vertical" size={4}>
            {uploadButton}
            <Button
              type="link"
              className="!px-0"
              size="small"
              icon={<LinkOutlined />}
              onClick={() => setShowUrlInput((prev) => !prev)}
            >
              {showUrlInput ? "收起图片链接" : "粘贴图片链接"}
            </Button>
          </Space>
        </div>
        {showUrlInput && (
          <Input
            size={size}
            value={value}
            onChange={(event) => onChange?.(event.target.value || undefined)}
            placeholder={placeholder}
            allowClear
          />
        )}
        <Text type="secondary">{emptyText}</Text>
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
        <Image src={value} alt={previewAlt} width={64} height={64} className="rounded border object-contain p-1" />
      ) : (
        <Text type="secondary">{emptyText}</Text>
      )}
    </Space>
  );
}
