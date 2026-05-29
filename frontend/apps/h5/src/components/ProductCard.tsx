interface ProductCardProps {
  productName: string;
  description: string;
  imageUrl?: string;
  images?: string[];
  showBadge?: boolean;
}

export function ProductCard({ productName, description, imageUrl, images, showBadge }: ProductCardProps) {
  const allImages = images?.length ? images : imageUrl ? [imageUrl] : [];

  return (
    <div className="mx-4 mt-4 rounded-2xl bg-white p-4 shadow-sm">
      {allImages.length > 1 ? (
        <div className="mb-3 flex snap-x snap-mandatory overflow-x-auto rounded-xl scrollbar-hide">
          {allImages.map((url, i) => (
            <div key={i} className="w-full shrink-0 snap-center">
              <img src={url} alt={`${productName} ${i + 1}`} className="h-48 w-full rounded-xl object-cover" />
            </div>
          ))}
        </div>
      ) : allImages[0] ? (
        <img src={allImages[0]} alt={productName} className="mb-3 h-48 w-full rounded-xl object-cover" />
      ) : null}
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
