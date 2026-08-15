/**
 * Three.js & WebGL Disposal and Lifecycle Cleanup Utilities
 *
 * Provides explicit, recursive, memory-leak-free teardown of:
 * - Three.js Scenes, Groups, and Object3D hierarchies
 * - Meshes, Line, and Points objects
 * - BufferGeometries, attributes, and indices
 * - Materials (single or multi-material arrays, shader uniforms)
 * - Textures, DataTextures, CanvasTextures, and Image sources
 * - WebGLRenderers and GPU context release (forceContextLoss / WEBGL_lose_context)
 * - HTMLCanvasElements and 2D/WebGL backbuffer zeroing
 */

/**
 * Disposes a Three.js Texture or texture-like object and releases GPU texture memory.
 */
export function disposeThreeTexture(texture: any): void {
  if (!texture) return;
  try {
    if (typeof texture.dispose === "function") {
      texture.dispose();
    }
    // Clean image / source references to prevent memory leaks in detached DOM / GPU buffers
    if ("image" in texture && texture.image) {
      if (
        typeof ImageBitmap !== "undefined" &&
        texture.image instanceof ImageBitmap &&
        typeof texture.image.close === "function"
      ) {
        texture.image.close();
      }
      texture.image = null;
    }
    if ("source" in texture && texture.source) {
      if (typeof texture.source.dispose === "function") {
        texture.source.dispose();
      }
      texture.source = null;
    }
    if ("mipmaps" in texture && Array.isArray(texture.mipmaps)) {
      texture.mipmaps.length = 0;
    }
  } catch (err) {
    // Avoid crashing on mock or non-standard texture objects
    console.warn("Error disposing Three.js texture:", err);
  }
}

/**
 * Disposes a Three.js Material or array of Materials, recursively disposing all attached texture maps.
 */
export function disposeThreeMaterial(material: any): void {
  if (!material) return;
  try {
    if (Array.isArray(material)) {
      material.forEach((mat) => disposeThreeMaterial(mat));
      return;
    }

    // List of known texture properties on Three.js materials
    const textureProperties = [
      "map",
      "alphaMap",
      "aoMap",
      "bumpMap",
      "displacementMap",
      "emissiveMap",
      "envMap",
      "gradientMap",
      "lightMap",
      "metalnessMap",
      "normalMap",
      "roughnessMap",
      "specularMap",
      "transmissionMap",
      "thicknessMap",
      "clearcoatMap",
      "clearcoatNormalMap",
      "clearcoatRoughnessMap",
      "sheenColorMap",
      "sheenRoughnessMap",
      "iridescenceMap",
      "iridescenceThicknessMap",
      "anisotropyMap",
    ];

    for (const prop of textureProperties) {
      if (material[prop]) {
        disposeThreeTexture(material[prop]);
        material[prop] = null;
      }
    }

    // Inspect custom shader uniforms for textures
    if (material.uniforms && typeof material.uniforms === "object") {
      for (const uniformKey of Object.keys(material.uniforms)) {
        const u = material.uniforms[uniformKey];
        if (u && u.value) {
          if (typeof u.value === "object" && typeof u.value.dispose === "function") {
            disposeThreeTexture(u.value);
            u.value = null;
          }
        }
      }
    }

    if (typeof material.dispose === "function") {
      material.dispose();
    }
  } catch (err) {
    console.warn("Error disposing Three.js material:", err);
  }
}

/**
 * Disposes a Three.js BufferGeometry or geometry-like object and its buffer attributes.
 */
export function disposeThreeGeometry(geometry: any): void {
  if (!geometry) return;
  try {
    // Dispose buffer attributes if any
    if (geometry.attributes && typeof geometry.attributes === "object") {
      for (const key of Object.keys(geometry.attributes)) {
        const attr = geometry.attributes[key];
        if (attr && typeof attr.dispose === "function") {
          attr.dispose();
        }
      }
    }
    if (geometry.index && typeof geometry.index.dispose === "function") {
      geometry.index.dispose();
    }
    if (typeof geometry.dispose === "function") {
      geometry.dispose();
    }
  } catch (err) {
    console.warn("Error disposing Three.js geometry:", err);
  }
}

/**
 * Disposes a Three.js Mesh, Line, Points, or Object3D and its associated geometries and materials.
 */
export function disposeThreeMesh(object: any): void {
  if (!object) return;
  try {
    if (object.geometry) {
      disposeThreeGeometry(object.geometry);
      object.geometry = null;
    }
    if (object.material) {
      disposeThreeMaterial(object.material);
      object.material = null;
    }
    if (typeof object.dispose === "function") {
      object.dispose();
    }
  } catch (err) {
    console.warn("Error disposing Three.js mesh:", err);
  }
}

/**
 * Recursively traverses a Three.js Scene or Object3D tree, disposing all child meshes,
 * geometries, materials, and textures, and clearing all child references.
 */
export function disposeThreeScene(scene: any): void {
  if (!scene) return;
  try {
    if (typeof scene.traverse === "function") {
      scene.traverse((child: any) => {
        if (child !== scene) {
          disposeThreeMesh(child);
        }
      });
    } else if (Array.isArray(scene.children)) {
      // Fallback traversal for non-standard or mock hierarchies
      const children = [...scene.children];
      for (const child of children) {
        disposeThreeScene(child);
        disposeThreeMesh(child);
      }
    }

    // Clear all children from scene
    if (typeof scene.clear === "function") {
      scene.clear();
    } else if (Array.isArray(scene.children)) {
      while (scene.children.length > 0) {
        const child = scene.children[0];
        if (typeof scene.remove === "function") {
          scene.remove(child);
        } else {
          scene.children.shift();
        }
      }
    }

    if (typeof scene.dispose === "function") {
      scene.dispose();
    }
  } catch (err) {
    console.warn("Error disposing Three.js scene:", err);
  }
}

/**
 * Safely queries a canvas context without throwing in headless/JSDOM environments.
 */
function getSafeCanvasContext(canvas: HTMLCanvasElement, contextId: string): any {
  try {
    return canvas.getContext(contextId as any);
  } catch {
    return null;
  }
}

/**
 * Disposes a WebGLRenderer, forces context loss, and unbinds its DOM canvas element.
 */
export function disposeWebGLRenderer(renderer: any): void {
  if (!renderer) return;
  try {
    // 1. Dispose renderer internal caches and render targets
    if (typeof renderer.dispose === "function") {
      renderer.dispose();
    }

    // 2. Force WebGL context loss to free hardware GPU context immediately
    if (typeof renderer.forceContextLoss === "function") {
      renderer.forceContextLoss();
    }

    // 3. Check for WebGL context on domElement or via getContext()
    let gl: any = null;
    try {
      if (typeof renderer.getContext === "function") {
        gl = renderer.getContext();
      } else if (renderer.domElement && typeof renderer.domElement.getContext === "function") {
        gl =
          getSafeCanvasContext(renderer.domElement, "webgl2") ||
          getSafeCanvasContext(renderer.domElement, "webgl") ||
          getSafeCanvasContext(renderer.domElement, "experimental-webgl");
      }
    } catch {
      gl = null;
    }

    if (gl) {
      const loseExt =
        typeof gl.getExtension === "function"
          ? gl.getExtension("WEBGL_lose_context")
          : null;
      if (loseExt && typeof loseExt.loseContext === "function") {
        loseExt.loseContext();
      }
    }

    // 4. Detach domElement reference
    if (renderer.domElement) {
      if (
        renderer.domElement.parentElement &&
        typeof renderer.domElement.parentElement.removeChild === "function"
      ) {
        try {
          renderer.domElement.parentElement.removeChild(renderer.domElement);
        } catch {
          // ignore if already removed
        }
      }
      renderer.domElement = null;
    }
  } catch (err) {
    console.warn("Error disposing WebGL renderer:", err);
  }
}

/**
 * Releases WebGL contexts (via WEBGL_lose_context extension), clears 2D contexts,
 * and zeroes out the canvas width/height dimensions to release backbuffers.
 */
export function disposeCanvas(canvas: HTMLCanvasElement | null): void {
  if (!canvas) return;
  try {
    // 1. Try to lose WebGL context if present
    if (typeof canvas.getContext === "function") {
      const gl =
        getSafeCanvasContext(canvas, "webgl2") ||
        getSafeCanvasContext(canvas, "webgl") ||
        getSafeCanvasContext(canvas, "experimental-webgl");

      if (gl) {
        const loseContextExt =
          typeof gl.getExtension === "function"
            ? gl.getExtension("WEBGL_lose_context")
            : null;
        if (loseContextExt && typeof loseContextExt.loseContext === "function") {
          loseContextExt.loseContext();
        }
      }

      // 2. If 2D context exists, clear backbuffer
      const ctx2d = getSafeCanvasContext(canvas, "2d");
      if (ctx2d && typeof ctx2d.clearRect === "function") {
        try {
          ctx2d.clearRect(0, 0, canvas.width, canvas.height);
        } catch {
          // ignore
        }
      }
    }

    // 3. Zero out canvas backbuffers
    canvas.width = 0;
    canvas.height = 0;
  } catch (err) {
    console.warn("Error disposing canvas:", err);
  }
}

