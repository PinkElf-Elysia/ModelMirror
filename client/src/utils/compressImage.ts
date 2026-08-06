const MAX_IMAGE_DIMENSION = 2048;
const MAX_DATA_URL_BYTES = 5 * 1024 * 1024;
const SUPPORTED_IMAGE_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/gif",
  "image/webp",
]);

function loadImage(src: string) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("图片读取失败，请换一张图片。"));
    image.src = src;
  });
}

function canvasToBlob(
  canvas: HTMLCanvasElement,
  type: "image/jpeg" | "image/png" | "image/webp",
  quality?: number,
) {
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          reject(new Error("图片压缩失败，请换一张图片。"));
          return;
        }
        resolve(blob);
      },
      type,
      quality,
    );
  });
}

function blobToDataUrl(blob: Blob) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") {
        resolve(reader.result);
        return;
      }
      reject(new Error("图片编码失败，请换一张图片。"));
    };
    reader.onerror = () => reject(new Error("图片编码失败，请换一张图片。"));
    reader.readAsDataURL(blob);
  });
}

function getScaledSize(width: number, height: number) {
  if (width <= MAX_IMAGE_DIMENSION && height <= MAX_IMAGE_DIMENSION) {
    return { width, height };
  }

  const ratio = Math.min(MAX_IMAGE_DIMENSION / width, MAX_IMAGE_DIMENSION / height);
  return {
    width: Math.round(width * ratio),
    height: Math.round(height * ratio),
  };
}

export async function compressImage(file: File): Promise<string> {
  if (!SUPPORTED_IMAGE_TYPES.has(file.type)) {
    throw new Error("仅支持 PNG、JPG、GIF、WebP 图片。");
  }

  const objectUrl = URL.createObjectURL(file);

  try {
    const image = await loadImage(objectUrl);
    const size = getScaledSize(image.naturalWidth, image.naturalHeight);
    const createCanvas = (withWhiteBackground: boolean) => {
      const canvas = document.createElement("canvas");
      canvas.width = size.width;
      canvas.height = size.height;
      const context = canvas.getContext("2d");
      if (!context) {
        throw new Error("当前浏览器不支持图片压缩。");
      }
      if (withWhiteBackground) {
        context.fillStyle = "#ffffff";
        context.fillRect(0, 0, size.width, size.height);
      }
      context.drawImage(image, 0, 0, size.width, size.height);
      return canvas;
    };

    const preferredType =
      file.type === "image/png"
        ? "image/png"
        : file.type === "image/webp"
          ? "image/webp"
          : "image/jpeg";
    const preferredCanvas = createCanvas(false);
    const qualities =
      preferredType === "image/png"
        ? [undefined]
        : [0.88, 0.8, 0.72, 0.64, 0.56, 0.48];

    for (const quality of qualities) {
      const blob = await canvasToBlob(
        preferredCanvas,
        preferredType,
        quality,
      );
      const dataUrl = await blobToDataUrl(blob);
      if (new Blob([dataUrl]).size <= MAX_DATA_URL_BYTES) {
        return dataUrl;
      }
    }

    if (preferredType === "image/png") {
      const jpegCanvas = createCanvas(true);
      for (const quality of [0.88, 0.8, 0.72, 0.64, 0.56, 0.48]) {
        const blob = await canvasToBlob(
          jpegCanvas,
          "image/jpeg",
          quality,
        );
        const dataUrl = await blobToDataUrl(blob);
        if (new Blob([dataUrl]).size <= MAX_DATA_URL_BYTES) {
          return dataUrl;
        }
      }
    }

    throw new Error("图片压缩后仍超过 5MB，请选择更小的图片。");
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}
