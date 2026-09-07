const isImage = (file) => /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(file.name);

export function documentUploadGroups(files) {
  const images = files.filter(isImage);
  if (images.length < 2) return files.map((file) => [file]);
  let grouped = false;
  return files.flatMap((file) => {
    if (!isImage(file)) return [[file]];
    if (grouped) return [];
    grouped = true;
    return [images];
  });
}
