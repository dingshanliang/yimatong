"use client";

import { useState } from "react";
import { App, Button, Image, Input, Space, Typography, Upload } from "antd";
import type { InputProps } from "antd";
import { UploadOutlined } from "@ant-design/icons";
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
}: ImageUploadInputProps) {
  const { message } = App.useApp();
  const [uploading, setUploading] = useState(false);

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
      </Space.Compact>
      {value ? (
        <Image src={value} alt={previewAlt} width={64} height={64} className="rounded border object-contain p-1" />
      ) : (
        <Text type="secondary">{emptyText}</Text>
      )}
    </Space>
  );
}
