export type ExtractedImageKind = "svg" | "data" | "url";

export interface ExtractedImage {
  id: string;
  kind: ExtractedImageKind;
  name: string;
  source: string;
  raw: string;
}

const markdownImageRegex = /!\[([^\]]*)\]\(([^)]+)\)/g;
const dataUrlRegex =
  /data:image\/(?:png|jpeg|jpg|webp|gif|svg\+xml)[;,][A-Za-z0-9+/=\-_\s]+/gi;
const svgRegex = /<svg[\s\S]*?<\/svg>/gi;
const bareImageUrlRegex =
  /\bhttps?:\/\/[^\s<>)"']+\.(?:png|jpe?g|webp|gif|svg)(?:\?[^\s<>)"']*)?/gi;

export function svgToDataURL(svg: string): string {
  const trimmed = svg.trim();
  const hasXmlns = /\sxmlns\s*=\s*["']http:\/\/www\.w3\.org\/2000\/svg/.test(
    trimmed,
  );
  const cleaned = hasXmlns
    ? trimmed
    : trimmed.replace(/^<svg/i, '<svg xmlns="http://www.w3.org/2000/svg"');
  return `data:image/svg+xml;utf8,${encodeURIComponent(cleaned)}`;
}

export function extensionOf(source: string, kind: ExtractedImageKind): string {
  if (kind === "svg") return "svg";
  if (kind === "data") {
    const match = source.match(/data:image\/([a-z0-9+]+);?/i);
    if (!match) return "png";
    const mime = match[1].toLowerCase();
    if (mime === "jpeg") return "jpg";
    if (mime === "svg+xml") return "svg";
    return mime;
  }
  const match = source.match(/\.([a-z0-9]+)(?:\?|#|$)/i);
  return match ? match[1].toLowerCase() : "png";
}

export function filenameForImage(img: ExtractedImage, index: number): string {
  const ext = extensionOf(img.source, img.kind);
  const safe =
    img.name.replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 40) ||
    `image-${index}`;
  return `${safe}.${ext}`;
}

function kindFromSource(source: string): ExtractedImageKind {
  const lowerSource = source.toLowerCase();
  if (lowerSource.startsWith("data:image/svg+xml")) return "svg";
  if (lowerSource.startsWith("data:image/")) return "data";
  return "url";
}

export function extractImages(text: string): {
  text: string;
  images: ExtractedImage[];
} {
  if (!text) return { text: "", images: [] };

  const images: ExtractedImage[] = [];
  let index = 0;

  let remaining = text.replace(
    markdownImageRegex,
    (match, alt: string, url: string) => {
      const source = url.trim();
      if (!source) return "";
      images.push({
        id: `ext-${index}`,
        kind: kindFromSource(source),
        name: alt.trim() || `图片-${index + 1}`,
        source,
        raw: match,
      });
      index += 1;
      return "";
    },
  );

  remaining = remaining.replace(dataUrlRegex, (match) => {
    const source = match.trim();
    const kind: ExtractedImageKind = source.toLowerCase().includes("svg+xml")
      ? "svg"
      : "data";
    images.push({
      id: `ext-${index}`,
      kind,
      name: `图片-${index + 1}`,
      source,
      raw: match,
    });
    index += 1;
    return "";
  });

  remaining = remaining.replace(svgRegex, (match) => {
    images.push({
      id: `ext-${index}`,
      kind: "svg",
      name: `SVG-${index + 1}`,
      source: svgToDataURL(match),
      raw: match,
    });
    index += 1;
    return "";
  });

  remaining = remaining.replace(bareImageUrlRegex, (match) => {
    const source = match.trim();
    images.push({
      id: `ext-${index}`,
      kind: "url",
      name: `图片-${index + 1}`,
      source,
      raw: match,
    });
    index += 1;
    return "";
  });

  return { text: remaining.trim(), images };
}

export async function downloadImage(
  source: string,
  filename: string,
): Promise<void> {
  try {
    if (source.startsWith("data:") || source.startsWith("blob:")) {
      const link = document.createElement("a");
      link.href = source;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      return;
    }

    const response = await fetch(source, { mode: "cors" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    window.alert(`下载失败：${message}\n\n请尝试右键图片，选择“图片另存为”。`);
  }
}

export async function svgDataUrlToPng(
  svgDataUrl: string,
  scale = 2,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      const width = Math.max(1, image.naturalWidth || 800) * scale;
      const height = Math.max(1, image.naturalHeight || 600) * scale;
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d");
      if (!context) {
        reject(new Error("Canvas 不可用"));
        return;
      }
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, width, height);
      context.drawImage(image, 0, 0, width, height);
      try {
        resolve(canvas.toDataURL("image/png"));
      } catch (error) {
        reject(error);
      }
    };
    image.onerror = () => reject(new Error("SVG 图片加载失败，无法转 PNG"));
    image.src = svgDataUrl;
  });
}
