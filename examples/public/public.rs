//! Example of the new, public API

use azul::{
    app::{App, AppConfig},
    css::Css,
    dom::Dom,
    window::WindowCreateOptions,
    callbacks::{RefAny, LayoutInfo},
};

struct Data {
    counter: usize,
}

fn layout(data: RefAny, _info: LayoutInfo) -> Dom {
    Dom::div()
}

fn main() {
    let data = Data {
        counter: 5,
    };
    let app = App::new(RefAny::new(data), AppConfig::new(), layout);
    app.run(WindowCreateOptions::new(Css::native()));
}