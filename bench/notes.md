<!-- stalecrate:begin -->
## Bevy changed after June 2026

This project pins Bevy 0.20.1. Your training data mostly covers 0.18.1 and earlier, so these APIs work differently from what you remember. Check here before using them.

### `bevy_shape` split out from `bevy_math`  (0.19 to 0.20; used in 153 files: `Aabb2d`, `Aabb2d::max`, `Aabb2d::min`, `Aabb2d::new`, `AabbCast2d`, `AabbCast2d::aabb`)

`bevy_shape` is a new crate centered around the geometric primitives provided
by bevy. These primitives and related traits have been split out from
`bevy_math` and are now available from different import paths than they used to
be.

Notably:

- all the `bevy_math::primitives::*` are now exposed either on the top level
  via `bevy_shape::*` or in the `bevy_shape::prelude::*`
- the following traits have also moved from `bevy_math` into `bevy_shape`
  - `Primitive2d` & `Primitive3d`
  - `Bounded2d` & `Bounded3d`
  - `BoundingVolume` & `IntersectsVolume`
  - `ToRing`
  - `Inset`
  - `ShapeSample`

If you use the `bevy::prelude::*`, there should be nothing you have to change
as all of this is still included in the general prelude. Otherwise you might
need to include a dependency on `bevy_shape` now and import your desired
structures from there.

### Avoiding unnecessary `AssetEvent::Modified` events that lead to rendering performance costs  (0.18 to 0.19; used in 52 files: `AssetEvent::Modified`, `Assets::get_mut`)

`Assets::get_mut` will now return `AssetMut<A: Asset>` instead of `&mut Asset`.
Similar to `Mut`/`ResMut`, the new implementation will trigger an `AssetEvent::Modified`
event only when the asset is actually mutated.

In some cases (like materials), triggering the `AssetEvent::Modified` event might lead to
measurable performance costs. To avoid this, you can check if the `Asset`
will change before mutating it:

```rust
fn update(
    query: Query<MeshMaterial3d<StandardMaterial>>,
    materials: ResMut<Assets<StandardMaterial>>,
    time: Res<Time>,
) {
    for material_handle in query.iter_mut() {
        // material variable now needs to be marked as mut
        let Some(mut material) = materials.get_mut(material_handle) else {
            continue;
        };

### Moved Observer events `B` Bundle generic into the event type  (0.19 to 0.20; used in 50 files: `Add`, `Insert`, `On`, `Remove`)

The `B: Bundle` type parameter has been removed from `On<E, B>` in observer systems.
Lifecycle events (`Add`/`Insert`/`Discard`/`Remove`/`Despawn`) have been updated
to have this `B: Bundle` directly on them.

```rust
// 0.19
world.add_observer(|on: On<Add, A>| {
    // ...
});

// 0.20
world.add_observer(|on: On<Add<A>>| {
    // ...
});
```

For custom event types that previously made use of `B: Bundle`, its recommended to do the following:

```rust
// 0.19

#[derive(Event)]
pub struct Foo;

#[derive(Component)]
pub struct Bar;

world.add_observer(|on: On<Foo, Bar>| {
    // ...
});

// 0.20

#[derive(Event)]
pub struct FooEvent;

#[derive(Component)]
pub struct Bar;

pub struct Foo<B: Bundle>(PhantomData<B>);

impl<B: Bundle> EventPattern for Foo<B> {
    type Event = FooEvent;
    type Components = B;
}

world.add_observer(|on: On<Foo<Bar>>| {
    // ...
});
```

### The old `bevy_scene` is now `bevy_world_serialization`  (0.18 to 0.19; used in 47 files: `DynamicScene`, `DynamicSceneRoot`, `InstanceId`, `SceneInstanceReady`, `SceneRoot`, `SceneSpawner`)

In **Bevy 0.19** we landed a subset of Bevy's Next Generation Scene system (known as BSN), which now lives in the `bevy_scene` / `bevy::scene` crate. However the old `bevy_scene` system still needs to stick around for a bit longer, as it provides some features that Bevy's Next Generation Scene system doesn't (yet!):

1. It is not _yet_ possible to write a World _to_ BSN, so the old system is still necessary for "round trip World serialization".
2. The GLTF scene loader has not yet been ported to BSN, so the old system is still necessary to spawn GLTF scenes in Bevy.

For this reason, we have renamed the old `bevy_scene` crate to `bevy_world_serialization`. If you were referencing `bevy_scene::*` or `bevy::scene::*` types, rename those paths to `bevy_world_serialization::*` and `bevy::world_serialization::*` respectively.

### `ui::widgets::Button` and `ui::Interaction` are deprecated  (0.19 to 0.20; used in 35 files: `Button`, `Interaction`)

`ui::widgets::Button`, available via the `ui::prelude`, has been deprecated in favor of `ui_widgets::Button`.
`ui::Interaction` has been deprecated in favor of the `picking::hover::Hovered` and `ui::Pressed` components.

View the updated `button.rs` example for updated `Button` usage patterns and how to use the `Hovered` and `Pressed` components.

### New crate `bevy_material`  (0.18 to 0.19; used in 32 files: `AlphaMode`, `BindGroupLayoutDescriptor`, `BindGroupLayoutEntries`, `ComputePipelineDescriptor`, `DrawFunctionId`, `FragmentState`)

Various material-related machinery was extracted from `bevy_pbr` and `bevy_render` into a new crate called `bevy_material`.

The following were moved from `bevy_render` to `bevy_material`:

- `AlphaMode`
- `SpecializedMeshPipelineError`

The following were moved from `bevy_pbr` to `bevy_material`:

- `OpaqueRendererMethod`
- `ErasedMeshPipelineKey`, `ErasedMaterialPipelineKey`, `ErasedMaterialKey`, `ErasedMaterialKeyVTable`, `RenderPhaseType`
- `MaterialProperties`

The following were moved from `bevy_render` to `bevy_material` but do not require migration thanks to re-exports:

### `Tonemapping` and `DebandDither` moved to `bevy_render::view`  (0.19 to 0.20; used in 22 files: `DebandDither`, `DebandDither::Enabled`, `MaterialBindGroupAllocator`, `MaterialBindGroupAllocator::allocate_unprepared`, `MaterialBindGroupAllocator::free`, `MaterialBindGroupAllocator::new`)

`Tonemapping` and `DebandDither` moved from `bevy_core_pipeline::tonemapping` to
`bevy_render::view`. Rust imports still work through re-exports, but the reflected type
paths changed: update scene files and Bevy Remote Protocol component keys that name them.

- `bevy_core_pipeline::tonemapping::Tonemapping` is now `bevy_render::view::Tonemapping`
- `bevy_core_pipeline::tonemapping::DebandDither` is now `bevy_render::view::DebandDither`

### Flat pointer events  (0.19 to 0.20; used in 21 files: `Pointer`)

Pointer events are now "flattened". For example `Pointer<Press>` is now `PointerPress`. These events no longer use (or implement) `Deref` to access the inner `Press` fields. Instead they are stored directly on the `PointerPress` event. `Pointer` is now non-generic, and is a field stored on each pointer event.

```rust
// 0.19
fn on_press(press: On<Pointer<Press>>) {
  info!("pressed {} {:?} {:?}", press.entity, press.pointer_id, press.pointer_location.position);
}

// 0.20
fn on_press(press: On<PointerPress>) {
  info!("pressed {} {:?} {:?}", press.entity, press.pointer.id, press.pointer.position);
}
```

`pointer.position` is just a `Vec2`, rather than a `Location`. `Location` isn't used much in practice. Consumers that need a `Location` can now use `pointer.location()`.

Developers that want to write code that is generic on pointer events should now use the new `PointerEvent` trait:

### `new_with_` prefix removed from `TextLayout` constructors  (0.18 to 0.19; used in 20 files: `TextLayout::new_with_justify`)

Constructor functions for the `TextLayout` type were simplified:

- `TextLayout::new_with_justify` -> `TextLayout::justify`
- `TextLayout::new_with_linebreak` -> `TextLayout::linebreak`
- `TextLayout::new_with_no_wrap` -> `TextLayout::no_wrap`

### `Tonemapping::None` is now a full passthrough  (0.19 to 0.20; used in 17 files: `Tonemapping`, `Tonemapping::None`)

`Tonemapping::None` is now a full passthrough. `ColorGrading` and `DebandDither` no longer
apply under it, and negative color channels are no longer clamped to zero. `Camera3d`
enables `DebandDither` by default, so a `Camera3d` without `Hdr` that used
`Tonemapping::None` renders differently. If you used `Tonemapping::None` to turn off the
tone curve, use the new `Tonemapping::Linear` instead. It applies no tone curve and keeps
grading, dither, and the clamp.

```rust
// 0.19
commands.spawn((Camera3d::default(), Tonemapping::None));

// 0.20
commands.spawn((Camera3d::default(), Tonemapping::Linear));
```

`Camera2d` now defaults to `Tonemapping::Linear`. No change is needed for 2D cameras. A
`Camera2d` with `Hdr` now runs the tonemapping pass.

Bevy logs a warning for a camera that combines `Tonemapping::None` with `DebandDither::Enabled`
or a non-default `ColorGrading`.

### `Hdr` moved to `bevy_camera`  (0.18 to 0.19; used in 15 files: `Hdr`)

`Hdr` has been moved from `bevy_render` to `bevy_camera`.

Furthermore, it is no longer extracted to the render world. If you were relying on its presence in the render world, consider using `ExtractedCamera::hdr` instead.

### Order independent transparency changes to support premultiplied alpha modes and per-material opt-out  (0.19 to 0.20; used in 14 files: `AlphaMode`, `AlphaMode::Add`, `AlphaMode::Blend`, `AlphaMode::Premultiplied`)

1. OIT now can be opt-out per material via the new `enable_oit` method on `Material` and `MaterialExtension`. The associated `ShaderDef` is `MATERIAL_OIT_ENABLED`. The original `OIT_ENABLED` is used for `mesh_view_bindings.wgsl` and is enabled per camera/view. So custom oit-compatible material shaders should gate `oit_draw` behind `MATERIAL_OIT_ENABLED` instead of `OIT_ENABLED`.
2. The `oit_draw` function now expects alpha-premultiplied color to support `AlphaMode::Premultiplied` and `AlphaMode::Add` in addition to `AlphaMode::Blend` modes.

```wgsl
// 0.19
#ifdef OIT_ENABLED
#import bevy_core_pipeline::oit::oit_draw
#endif // OIT_ENABLED

### Also changed (see the migration guides)

- `Atmosphere` has been moved to `bevy_light` (0.18 to 0.19)
- Light gizmos have been moved from `bevy_gizmos` to `bevy_light` (0.18 to 0.19)
- `Skybox` `image` is now optional (0.18 to 0.19)
- `Skybox` moved to `bevy_light` (0.18 to 0.19)
- Several built-in schedules now order their system sets weakly (0.19 to 0.20)
- `bevy_curve` split out from `bevy_math` (0.19 to 0.20)
- `ExtractComponent` refactor (0.18 to 0.19)
- Transmission has been moved to `bevy_pbr` (0.18 to 0.19)
- Render Graph as Systems (0.18 to 0.19)
- Resources as Components (0.18 to 0.19)
- New `Node::direction` field (0.18 to 0.19)
- `MainEntityHashMap`/`MainEntityHashSet` alias update (0.19 to 0.20)
- RelationshipAccessor improvements (0.18 to 0.19)
- Extraction is now generic over worlds (0.19 to 0.20)
- Advanced AssetServer load variants are now exposed through a builder pattern. (0.18 to 0.19)
- `Core` prefix removed from UI widget components (0.18 to 0.19)
- `UnpreparedBindGroup` is now `BindGroupBuilder` (0.19 to 0.20)
- `ShaderStorageBuffer` renamed to `ShaderBuffer` (0.18 to 0.19)
- Invert `bevy_gltf` dependency with `bevy_pbr` (0.18 to 0.19)
- Lifecycle event changes (0.18 to 0.19)
- `set_executor` replaced `ExecutorKind` (0.18 to 0.19)
- `Command` error handling has been simplified (0.18 to 0.19)
- `Image::pixel_bytes` and `Image::pixel_data_offset` now return `Result` (0.18 to 0.19)
- `Atmosphere` is now an entity (0.18 to 0.19)
- Changes have been made to `DepthAttachment`, `ViewDepthTexture` and `ViewPrepassTextures::depth` to accommodate stencil support (0.19 to 0.20)
- DynamicSceneBuilder and DynamicScene::from_scene now require a &TypeRegistry (0.18 to 0.19)

### Other changes found in the API diff

- `TextFont::font_size` changed: `font_size: f32` → `font_size: FontSize` (used in 72 files)
- `PointLight::shadows_enabled` was removed in bevy_light 0.20.1; `PointLight` gained: `contact_shadows_enabled`, `shadow_maps_enabled` (used in 53 files)
- `TextFont::font` changed: `font: Handle<Font>` → `font: FontSource` (used in 40 files)
- `DirectionalLight::shadows_enabled` was removed in bevy_light 0.20.1; `DirectionalLight` gained: `contact_shadows_enabled`, `shadow_maps_enabled` (used in 38 files)
- `observe` changed: `fn observe(observer: impl IntoObserverSystem) -> impl EntityCommand` → `fn observe(observer: impl IntoEntityObserver) -> impl EntityCommand` (used in 33 files)
- `Interaction::Pressed` was removed in bevy_ui 0.20.1 (used in 22 files)
- `Click` was removed in bevy_picking 0.20.1; new in `bevy_picking::events`: `PointerClick`, `HoveredEntityAncestors`, `PointerCancel`, `PointerDrag`, `PointerDragDrop` (used in 19 files)
- `AssetServer::load_with_settings` was removed in bevy_asset 0.20.1; `AssetServer` gained: `are_dependencies_loaded`, `are_direct_dependencies_loaded`, `load_builder` (used in 18 files)
- `Interaction::Hovered` was removed in bevy_ui 0.20.1 (used in 18 files)
- `MessageWriter::write` changed: `fn write(self, message: E) -> MessageId<E>` → `fn write(self, message: M) -> MessageId<M>` (used in 17 files)
- `BorderRadius::all` changed: `fn all(radius: Val) -> Self` → `fn all(radius: impl Into) -> Self` (used in 17 files)
- `Pointer::entity` was removed in bevy_picking 0.20.1; `Pointer` gained: `id`, `location`, `position`, `target` (used in 16 files)
- `Interaction::None` was removed in bevy_ui 0.20.1 (used in 16 files)
- `App::add_observer` changed: `fn add_observer(self, observer: impl IntoObserverSystem) -> &mut Self` → `fn add_observer(self, observer: impl IntoObserver) -> &mut Self` (used in 14 files)
- `Val::Px` changed signature in bevy_ui 0.20.1 (used in 12 files)
- `SpotLight::shadows_enabled` was removed in bevy_light 0.20.1; `SpotLight` gained: `contact_shadows_enabled`, `shadow_maps_enabled` (used in 10 files)
- `Over` was removed in bevy_picking 0.20.1; new in `bevy_picking::events`: `PointerDragOver`, `PointerOver`, `HoveredEntityAncestors`, `PointerCancel`, `PointerClick` (used in 10 files)
- `On::trigger` changed: `fn trigger(self) -> &E::Trigger` → `fn trigger(self) -> &EventPatternTrigger<'t, E>` (used in 9 files)
- `RenderPipelineDescriptor::vertex` was removed in bevy_render 0.20.1 (used in 9 files)
- `ClearColorConfig::Custom` changed signature in bevy_camera 0.20.1 (used in 8 files)
- `Projection::Perspective` changed signature in bevy_camera 0.20.1 (used in 8 files)
- `ImageSampler::Descriptor` changed signature in bevy_image 0.20.1 (used in 8 files)
- `RenderLabel` was removed in bevy_render 0.20.1 (used in 8 files)
- `RenderPipelineDescriptor::fragment` was removed in bevy_render 0.20.1 (used in 8 files)
- `RenderPipelineDescriptor::layout` was removed in bevy_render 0.20.1 (used in 8 files)
- `Pointer::event` was removed in bevy_picking 0.20.1; `Pointer` gained: `id`, `location`, `position`, `target` (used in 7 files)
- `NodeRunError` was removed in bevy_render 0.20.1 (used in 7 files)
- `RenderGraphContext` was removed in bevy_render 0.20.1 (used in 7 files)
- `TextFont::from_font_size` changed: `fn from_font_size(font_size: f32) -> Self` → `fn from_font_size(font_size: impl Into) -> Self` (used in 7 files)
- `RenderTarget::Image` changed signature in bevy_camera 0.20.1 (used in 6 files)
- `FogFalloff::Linear` changed signature in bevy_pbr 0.20.1 (used in 6 files)
- `RenderPipelineDescriptor::label` was removed in bevy_render 0.20.1 (used in 6 files)
- `BorderRadius::left` changed: `fn left(radius: Val) -> Self` → `fn left(radius: impl Into) -> Self` (used in 6 files)
- `Projection::Orthographic` changed signature in bevy_camera 0.20.1 (used in 5 files)
- `MaterialProperties::shadows_enabled` was removed in bevy_pbr 0.20.1 (used in 5 files)
- `FragmentState::targets` was removed in bevy_render 0.20.1 (used in 5 files)
- `RenderPipelineDescriptor::primitive` was removed in bevy_render 0.20.1 (used in 5 files)
- `Scene::world` was removed in bevy_scene 0.20.1; `Scene` gained: `register_dependencies`, `resolve` (used in 5 files)
- `Volume` was removed in bevy_image 0.20.1; new in `bevy_image`: `ImageSaver`, `ImageSaverSettings`, `SaveImageError`, `SaveImageFormatSetting`, `TextureAtlasTemplate` (used in 4 files)
- `Indices::U32` changed signature in bevy_mesh 0.20.1 (used in 4 files)
<!-- stalecrate:end -->
