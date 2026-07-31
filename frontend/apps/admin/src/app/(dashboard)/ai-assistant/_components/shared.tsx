"use client";

import { Card, Tag } from "antd";
import { STATUS_COLORS } from "@/lib/status-colors";

export function GenerationIdTag({ id }: { id: string }) {
  return (
    <Tag color={STATUS_COLORS.processing} className="mt-2">
      生成 ID: {id}
    </Tag>
  );
}

export function ResultCard({
  title,
  icon,
  children,
  generationId,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  generationId?: string;
}) {
  return (
    <Card
      className="mt-4"
      title={
        <span>
          {icon} {title}
        </span>
      }
    >
      {children}
      {generationId && <GenerationIdTag id={generationId} />}
    </Card>
  );
}
