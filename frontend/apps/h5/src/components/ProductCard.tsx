"use client";

import { useState } from "react";

interface ProductCardProps {
  productName: string;
  description: string;
  image_url?: string;
  imageUrl?: string;
  images?: string[];
  showBadge?: boolean;
}

export function ProductCard({ productName, description, image_url, imageUrl, images, showBadge }: ProductCardProps) {
  const [imgError, setImgError] = useState(false);

  // 确定图片源：优先 image_url，兜底 imageUrl，最后 images[0]
  const imageSource = image_url || imageUrl || (images?.[0]) || "";
  const showImage = imageSource && !imgError;

  return (
    <div className="mx-4 mt-4 rounded-2xl bg-white p-4 shadow-sm">
      {showImage ? (
        <img
          src={imageSource}
          alt={productName || "产品图片"}
          className="mb-3 h-48 w-full rounded-xl object-cover"
          onError={() => setImgError(true)}
        />
      ) : (
        <div className="mb-3 flex h-48 items-center justify-center rounded-xl bg-gray-100">
          <span className="text-5xl text-gray-300">🌾</span>
        </div>
      )}
      <h1 className="text-xl font-bold text-gray-900">{productName || "产品信息"}</h1>
      {description && <p className="mt-1 text-sm text-gray-500 leading-relaxed">{description}</p>}
      {showBadge && (
        <div className="mt-2 flex items-center gap-2">
          <span className="inline-flex items-center rounded-full bg-green-50 px-2.5 py-0.5 text-xs font-medium text-green-700">
            正品保障
          </span>
          <span className="inline-flex items-center rounded-full bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-700">
            已验证
          </span>
        </div>
      )}
    </div>
  );
}
