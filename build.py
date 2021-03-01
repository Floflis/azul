import json
import re
import pprint
import os
import subprocess
import shutil
from sys import platform

# dict that keeps the order of insertion
from collections import OrderedDict

def create_folder(path):
    os.mkdir(path)

def remove_path(path):
    """ param <path> could either be relative or absolute. """
    if os.path.isfile(path) or os.path.islink(path):
        os.remove(path)  # remove the file
    elif os.path.isdir(path):
        shutil.rmtree(path)  # remove dir and all contains
    else:
        raise ValueError("file {} is not a file or dir.".format(path))

def zip_directory(output_filename, dir_name):
    shutil.make_archive(output_filename, 'zip', dir_name)

def read_file(path):
    text_file = open(path, 'r')
    text_file_contents = text_file.read()
    text_file.close()
    return text_file_contents

def read_api_file(path):
    api_file_contents = read_file(path)
    apiData = json.loads(api_file_contents)
    return apiData

root_folder = os.path.abspath(os.path.join(__file__, os.pardir))
prefix = "Az"
fn_prefix = "az_"
basic_types = [ # note: "char" is not a primitive type! - use u32 instead
    "bool", "f32", "f64", "fn", "i128", "i16",
    "i32", "i64", "i8", "isize", "slice", "u128", "u16",
    "u32", "u64", "u8", "()", "usize", "c_void"
]

license = read_file(root_folder + "/LICENSE")

rust_api_patches = {
    tuple(['str']): read_file(root_folder + "/api/_patches/azul.rs/string.rs"),
    tuple(['vec']): read_file(root_folder + "/api/_patches/azul.rs/vec.rs"),
    tuple(['option']): read_file(root_folder + "/api/_patches/azul.rs/option.rs"),
    tuple(['dom']): read_file(root_folder + "/api/_patches/azul.rs/dom.rs"),
    tuple(['gl']): read_file(root_folder + "/api/_patches/azul.rs/gl.rs"),
    tuple(['css']): read_file(root_folder + "/api/_patches/azul.rs/css.rs"),
    tuple(['window']): read_file(root_folder + "/api/_patches/azul.rs/window.rs"),
    tuple(['callbacks']): read_file(root_folder + "/api/_patches/azul.rs/callbacks.rs"),
}

# ---------------------------------------------------------------------------------------------

def to_snake_case(name):
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

# turns a list of function args into function pointer args
# ex. "mut dom: AzDom, event: AzEventFilter, data: AzRefAny, callback: AzCallback"
# ->  "_: AzDom, _: AzEventFilter, _: AzRefAny, _: AzCallback"
def strip_fn_arg_types(arg_list):
    if len(arg_list) == 0:
        return ""

    arg_list1 = ""

    for item in arg_list.split(","):
        part_b = item.split(":")[1]
        arg_list1 += "_: " + part_b + ", "

    if arg_list1 != "":
        arg_list1 = arg_list1[:-2]

    return arg_list1.strip()

def write_file(string, path):
    text_file = open(path, "w+", newline='')
    text_file.write(string)
    text_file.close()

def is_primitive_arg(arg):
    return get_stripped_arg(arg) in basic_types

def get_stripped_arg(arg):
    arg = arg.replace("&", "")
    arg = arg.replace("&mut", "")
    arg = arg.replace("*const", "")
    arg = arg.replace("*const", "")
    arg = arg.replace("*mut", "")
    arg = arg.strip()
    return arg

def search_imports_arg_type(c, search_type, arg_types_to_search):
    if search_type in c.keys():
        for fn_name in c[search_type]:
            const = c[search_type][fn_name]
            if "fn_args" in const.keys():
                for arg_object in const["fn_args"]:
                    arg_name = list(arg_object.keys())[0]
                    if arg_name == "self":
                        continue
                    arg_type = arg_object[arg_name]
                    arg_types_to_search.append(arg_type)

def get_all_imports(apiData, module, module_name):

    imports = {}

    arg_types_to_search = []

    for class_name in module.keys():
        c = module[class_name]
        search_imports_arg_type(c, "constructors", arg_types_to_search)
        search_imports_arg_type(c, "functions", arg_types_to_search)

    for arg in arg_types_to_search:

        arg = arg.replace("*const", "")
        arg = arg.replace("*mut", "")
        arg = arg.strip()

        if arg in basic_types:
            continue

        found_module = search_for_class_by_class_name(apiData, arg)

        if found_module is None:
            raise Exception(arg + " not found!")

        if found_module[0] in imports:
            imports[found_module[0]].add(found_module[1])
        else:
            imports[found_module[0]] = {found_module[1]}

    if module_name in imports:
        del imports[module_name]

    imports_str = ""

    for module_name in imports.keys():
        classes = list(imports[module_name])
        use_str = ""
        if len(classes) == 1:
            use_str = classes[0]
        else:
            use_str = "{"
            classes.sort()
            for c in classes:
                use_str += c + ", "
            use_str = use_str[:-2]
            use_str += "}"

        imports_str += "    use crate::" + module_name + "::" + use_str + ";\r\n"

    return imports_str

def fn_args_c_api(f, class_name, class_ptr_name, self_as_first_arg, apiData):
    fn_args = ""

    if self_as_first_arg:
        self_val = list(f["fn_args"][0].values())[0]
        if (self_val == "value"):
            fn_args += class_name.lower() + ": " + class_ptr_name + ", "
        elif (self_val == "mut value"):
            fn_args += "mut " + class_name.lower() + ": " + class_ptr_name + ", "
        elif (self_val == "refmut"):
            fn_args += class_name.lower() + ": &mut " + class_ptr_name + ", "
        elif (self_val == "ref"):
            fn_args += class_name.lower() + ": &" + class_ptr_name + ", "
        else:
            raise Exception("wrong self value " + self_val)

    if "fn_args" in f.keys():
        for arg_object in f["fn_args"]:
            arg_name = list(arg_object.keys())[0]
            if arg_name == "self":
                continue
            arg_type = arg_object[arg_name]

            analyzed_arg_type = analyze_type(arg_type)
            ptr_type = analyzed_arg_type[0]
            arg_type = analyzed_arg_type[1]

            if is_primitive_arg(arg_type):
                fn_args += arg_name + ": " + ptr_type + arg_type + ", " # no pre, no postfix
            else:
                arg_type_new = search_for_class_by_class_name(apiData, arg_type)
                if arg_type_new is None:
                    print("arg_type not found: " + str(arg_type))
                    raise Exception("type not found: " + arg_type)
                arg_type = arg_type_new[1]
                fn_args += arg_name + ": " + ptr_type + prefix + arg_type + ", " # no postfix
        fn_args = fn_args[:-2]

    return fn_args

def analyze_type(arg):
    starts = ""
    arg_type = ""
    ends = ""

    if type(arg) is dict:
        print("expected string, got dict: " + str(arg))

    if arg.startswith("&mut"):
        starts = "&mut "
        arg_type = arg.replace("&mut", "")
    elif arg.startswith("&"):
        starts = "&"
        arg_type = arg.replace("&", "")
    elif arg.startswith("*const"):
        starts = "*const "
        arg_type = arg.replace("*const", "")
    elif arg.startswith("*mut"):
        starts = "*mut "
        arg_type = arg.replace("*mut", "")
    else:
        arg_type = arg

    arg_type = arg_type.strip()

    if arg_type.startswith("[") and arg_type.endswith("]"):
        starts += "["
        arg_type_array = arg_type[1:].split(";")
        arg_type = arg_type_array[0]
        ends += ";" + arg_type_array[1]

    return [starts, arg_type, ends]

def class_is_small_enum(c):
    return "enum_fields" in c.keys()

def class_is_small_struct(c):
    return "struct_fields" in c.keys()

def class_is_typedef(c):
    return "callback_typedef" in c.keys()

def class_is_stack_allocated(c):
    class_is_boxed_object = not("external" in c.keys() and ("struct_fields" in c.keys() or "enum_fields" in c.keys() or "callback_typedef" in c.keys() or "const" in c.keys()))
    return not(class_is_boxed_object)

# Find the [module, classname] given a class_name, returns None if not found
# Then you can use get_class() to get the class object
def search_for_class_by_class_name(api_data, searched_class_name):
    for module_name in api_data.keys():
        module = api_data[module_name]
        for class_name in module["classes"].keys():
            c = module["classes"][class_name]
            class_name = class_name
            if class_name == searched_class_name or class_name == searched_class_name:
                return [module_name, class_name]

    return None

def get_class(api_data, module_name, class_name):
    return api_data[module_name]["classes"][class_name]

# Returns whether a type is external, searches by class_name instead of class_name
def is_stack_allocated_type(api_data, class_name):
    search_result = search_for_class_by_class_name(api_data, class_name)
    if search_result is None:
        raise Exception("type not found " + class_name)
    c = get_class(api_data, search_result[0], search_result[1])
    return class_is_stack_allocated(c)

# Returns if the class is "pure virtual", i.e. if it is an
# object consisting of patches instead of being defined in the API
def class_is_virtual(api_data, className, api):
    for module_name in api_data.keys():
        module = api_data[module_name]["classes"]
        for class_name in module.keys():
            if class_name != className:
                continue
            c = module[class_name]
            if "use_patches" in c.keys() and api in c["use_patches"]:
                return True

    return False

# Generate the string for TAKING rust-api function arguments
def rust_bindings_fn_args(f, class_name, class_ptr_name, self_as_first_arg, api_data):
    fn_args = ""

    if self_as_first_arg:
        self_val = list(f["fn_args"][0].values())[0]
        if (self_val == "value") or (self_val == "mut value"):
            fn_args += "self, "
        elif (self_val == "refmut"):
            fn_args += "&mut self, "
        elif (self_val == "ref"):
            fn_args += "&self, "
        else:
            raise Exception("wrong self value " + self_val)

    if "fn_args" in f.keys():
        for arg_object in f["fn_args"]:
            arg_name = list(arg_object.keys())[0]
            if arg_name == "self":
                continue
            arg_type = arg_object[arg_name]
            arg_type = arg_type.strip()

            type_analyzed = analyze_type(arg_type)
            start = type_analyzed[0]
            arg_type = type_analyzed[1]

            if is_primitive_arg(arg_type):
                fn_args += arg_name + ": " + start + arg_type + ", " # usize
            else:
                arg_type_class_name = search_for_class_by_class_name(api_data, arg_type)
                if arg_type_class_name is None:
                    raise Exception("arg type " + arg_type + " not found!")
                arg_type_class = get_class(api_data, arg_type_class_name[0], arg_type_class_name[1])

                if start == "*const " or start == "*mut ":
                    fn_args += arg_name + ": " + start + prefix + arg_type_class_name[1] + ", "
                else:
                    fn_args += arg_name + ": " + start + arg_type_class_name[1] + ", "

        fn_args = fn_args[:-2]

    return fn_args

# Generate the string for CALLING rust-api function args
def rust_bindings_call_fn_args(f, class_name, class_ptr_name, self_as_first_arg, api_data, class_is_boxed_object):
    fn_args = ""
    if self_as_first_arg:
        self_val = list(f["fn_args"][0].values())[0]
        fn_args += "self, "

    if "fn_args" in f.keys():
        for arg_object in f["fn_args"]:
            arg_name = list(arg_object.keys())[0]
            if arg_name == "self":
                continue

            arg_type = arg_object[arg_name].strip()
            starts = ""
            type_analyzed = analyze_type(arg_type)
            start = type_analyzed[0]
            arg_type = type_analyzed[1]

            if is_primitive_arg(arg_type):
                fn_args += arg_name + ", "
            else:
                arg_type = arg_type.strip()
                arg_type_class = search_for_class_by_class_name(api_data, arg_type)
                if arg_type_class is None:
                    raise Exception("arg type " + arg_type + " not found!")
                arg_type_class = get_class(api_data, arg_type_class[0], arg_type_class[1])

                if start == "*const " or start == "*mut ":
                    fn_args += arg_name + ", "
                else:
                    if class_is_typedef(arg_type_class):
                        fn_args += start + arg_name + ", "
                    elif class_is_stack_allocated(arg_type_class):
                        fn_args += start + arg_name + ", " # .object
                    else:
                        fn_args += start + arg_name + ", "

        fn_args = fn_args[:-2]

    return fn_args


# ---------------------------------------------------------------------------------------------


# Generates the azul-dll/lib.rs file
#
# Returns an array:
#
#   [
#      dll.rs code,
#      all structs (in order of dependency),
#      all functions (in order of appearance),
#      C forward_declarations,
#   ]
#
def generate_rust_dll(api_data):

    version = list(api_data.keys())[-1]
    code = ""
    code += "#![crate_type = \"cdylib\"]\r\n"
    code += "#![deny(improper_ctypes_definitions)]"
    code += "\r\n"
    code += "// WARNING: autogenerated code for azul api version " + str(version) + "\r\n"
    code += "\r\n\r\n"

    api_data = api_data[version]

    structs_map = {}
    functions_map = {}

    code += """
        extern crate azul_core;

        #[cfg(target_arch = "wasm32")]
        extern crate azul_web as azul_impl;
        #[cfg(not(target_arch = "wasm32"))]
        extern crate azul_desktop as azul_impl;

        use core::ffi::c_void;
        use azul_impl::{
            css::{self, *},
            callbacks::RefAny,
            window::{WindowCreateOptions, WindowState},
            resources::{RawImage, AppConfig, FontId, ImageId},
            app::App,
            task::{Timer, TimerId},
            gl::TextureFlags,
        };
    """

    for module_name in api_data.keys():
        module = api_data[module_name]["classes"]

        for class_name in module.keys():
            c = module[class_name]

            code += "\r\n"

            class_is_boxed_object = not(class_is_stack_allocated(c))
            class_is_const = "const" in c.keys()
            class_can_be_cloned = True
            if "clone" in c.keys():
                class_can_be_cloned = c["clone"]

            struct_derive = []
            if "derive" in c.keys():
                struct_derive = c["derive"]

            class_can_derive_debug = "derive" in c.keys() and "Debug" in c["derive"]
            class_can_be_copied = "derive" in c.keys() and "Copy" in c["derive"]
            class_has_partialeq = "derive" in c.keys() and "PartialEq" in c["derive"]
            class_has_eq = "derive" in c.keys()and "Eq" in c["derive"]
            class_has_partialord = "derive" in c.keys()and "PartialOrd" in c["derive"]
            class_has_ord = "derive" in c.keys() and "Ord" in c["derive"]
            class_can_be_hashed = "derive" in c.keys() and "Hash" in c["derive"]

            class_has_custom_destructor = "custom_destructor" in c.keys() and c["custom_destructor"]
            class_is_callback_typedef = "callback_typedef" in c.keys() and (len(c["callback_typedef"].keys()) > 0)
            is_boxed_object = "is_boxed_object" in c.keys() and c["is_boxed_object"]
            treat_external_as_ptr = "external" in c.keys() and is_boxed_object

            # Small structs and enums are stack-allocated in order to save on indirection
            # They don't have destructors, since they
            c_is_stack_allocated = not(class_is_boxed_object)

            class_ptr_name = prefix + class_name

            if class_is_callback_typedef:
                code += "pub type " + class_ptr_name + " = " + generate_rust_callback_fn_type(api_data, c["callback_typedef"]) + ";"
                structs_map[class_ptr_name] = { "callback_typedef": c["callback_typedef"] }
                continue

            struct_doc = ""
            if "doc" in c.keys():
                struct_doc = c["doc"]
            else:
                if c_is_stack_allocated:
                    struct_doc = "Re-export of rust-allocated (stack based) `" + class_name + "` struct"
                else:
                    struct_doc = "Pointer to rust-allocated `Box<" + class_name + ">` struct"

            code += "/// " + struct_doc  + "\r\n"

            if "external" in c.keys():
                external_path = c["external"]
                if class_is_const:
                    code += "pub static " + class_ptr_name + ": " + prefix + c["const"] + " = " + external_path + ";\r\n"
                elif class_is_boxed_object:
                    structs_map[class_ptr_name] = {"external": external_path, "clone": class_can_be_cloned, "is_boxed_object": is_boxed_object, "custom_destructor": class_has_custom_destructor, "derive": struct_derive, "doc": struct_doc, "struct": [{"ptr": {"type": "*mut c_void" }}]}
                    if treat_external_as_ptr:
                        code += "pub type " + class_ptr_name + "TT = " + external_path + ";\r\n"
                        code += "pub use " + class_ptr_name + "TT as " + class_ptr_name + ";\r\n"
                    else:
                        code += "#[repr(C)] pub struct " + class_ptr_name + " { pub ptr: *mut c_void }\r\n"
                else:
                    if "struct_fields" in c.keys():
                        structs_map[class_ptr_name] = {"external": external_path, "clone": class_can_be_cloned, "is_boxed_object": is_boxed_object, "custom_destructor": class_has_custom_destructor, "derive": struct_derive, "doc": struct_doc, "struct": c["struct_fields"]}
                    elif "enum_fields" in c.keys():
                        structs_map[class_ptr_name] = {"external": external_path, "clone": class_can_be_cloned, "is_boxed_object": is_boxed_object, "custom_destructor": class_has_custom_destructor, "derive": struct_derive, "doc": struct_doc, "enum": c["enum_fields"]}

                    code += "pub type " + class_ptr_name + "TT = " + external_path + ";\r\n"
                    code += "pub use " + class_ptr_name + "TT as " + class_ptr_name + ";\r\n"
            else:
                raise Exception("structs without 'external' key are not allowed!")

            if "constructors" in c.keys():
                for fn_name in c["constructors"]:

                    const = c["constructors"][fn_name]

                    fn_body = ""

                    if c_is_stack_allocated:
                        fn_body += const["fn_body"]
                    else:
                        fn_body += "let object: " + class_name + " = " + const["fn_body"] + "; " # note: security check, that the returned object is of the correct type
                        fn_body += "let ptr = Box::into_raw(Box::new(object)) as *mut c_void; "
                        fn_body += class_ptr_name + " { ptr }"

                    if "doc" in const.keys():
                        code += "/// " + const["doc"] + "\r\n"
                    else:
                        code += "/// Creates a new `" + class_name + "` instance whose memory is owned by the rust allocator\r\n"
                        code += "/// Equivalent to the Rust `" + class_name  + "::" + fn_name + "()` constructor.\r\n"

                    returns = class_ptr_name
                    if "returns" in const.keys():
                        return_type = const["returns"]["type"]
                        analyzed_return_type = analyze_type(return_type)
                        if is_primitive_arg(analyzed_return_type[1]):
                            returns = return_type
                        else:
                            return_type_class = search_for_class_by_class_name(api_data, analyzed_return_type[1])
                            if return_type_class is None:
                                print("rust-dll: (line 549): no return_type_class found for " + return_type)

                            returns = analyzed_return_type[0] + prefix + return_type_class[1] + analyzed_return_type[2] # no postfix


                    fn_args = fn_args_c_api(const, class_name, class_ptr_name, False, api_data)

                    functions_map[str(to_snake_case(class_ptr_name) + "_" + fn_name)] = [fn_args, returns];
                    code += "#[no_mangle] pub extern \"C\" fn " + to_snake_case(class_ptr_name) + "_" + fn_name + "(" + fn_args + ") -> " + returns + " { "
                    code += fn_body
                    code += " }\r\n"

            if "functions" in c.keys():
                for fn_name in c["functions"]:

                    f = c["functions"][fn_name]

                    fn_body = f["fn_body"]

                    if "doc" in f.keys():
                        code += "/// " + f["doc"] + "\r\n"
                    else:
                        code += "/// Equivalent to the Rust `" + class_name  + "::" + fn_name + "()` function.\r\n"

                    fn_args = fn_args_c_api(f, class_name, class_ptr_name, True, api_data)

                    returns = ""
                    if "returns" in f.keys():
                        return_type = f["returns"]["type"]
                        analyzed_return_type = analyze_type(return_type)
                        if is_primitive_arg(analyzed_return_type[1]):
                            returns = return_type
                        else:
                            return_type_class = search_for_class_by_class_name(api_data, analyzed_return_type[1])
                            if return_type_class is None:
                                print("rust-dll: (line 549): no return_type_class found for " + return_type)

                            returns = analyzed_return_type[0] + prefix + return_type_class[1] + analyzed_return_type[2] # no postfix

                    functions_map[str(to_snake_case(class_ptr_name) + "_" + fn_name)] = [fn_args, returns];
                    return_arrow = "" if returns == "" else " -> "
                    code += "#[no_mangle] pub extern \"C\" fn " + to_snake_case(class_ptr_name) + "_" + fn_name + "(" + fn_args + ")" + return_arrow + returns + " { "
                    code += fn_body
                    code += " }\r\n"

            if c_is_stack_allocated:
                if class_can_be_copied:
                    # intentionally empty, no destructor necessary
                    pass
                elif class_has_custom_destructor or treat_external_as_ptr:
                    # az_item_delete()
                    code += "/// Destructor: Takes ownership of the `" + class_name + "` pointer and deletes it.\r\n"
                    functions_map[str(to_snake_case(class_ptr_name) + "_delete")] = ["object: &mut " + class_ptr_name, ""];
                    code += "#[no_mangle] pub extern \"C\" fn " + to_snake_case(class_ptr_name) + "_delete(object: &mut " + class_ptr_name + ") { "
                    code += " unsafe { core::ptr::drop_in_place(object); } "
                    code += "}\r\n"

                if treat_external_as_ptr and class_can_be_cloned:
                    # az_item_deep_copy()
                    code += "/// Clones the object\r\n"
                    functions_map[str(to_snake_case(class_ptr_name) + "_deep_copy")] = ["object: &" + class_ptr_name, class_ptr_name];
                    code += "#[no_mangle] pub extern \"C\" fn " + to_snake_case(class_ptr_name) + "_deep_copy(object: &" + class_ptr_name + ") -> " + class_ptr_name + " { "
                    code += "object.clone()"
                    code += " }\r\n"
            else:
                raise Exception("type " + class_name + "is not stack allocated!")

    sort_structs_result = sort_structs_map(structs_map)
    structs_map = sort_structs_result[0]
    forward_delcarations = sort_structs_result[1]

    code += "\r\n\r\n"
    code += generate_size_test(api_data, structs_map)

    return [code, structs_map, functions_map, forward_delcarations]

# Returns a sorted structs map where the structs are sorted
# so that all structs that a class depends on as fields appear
# before the class itself
#
# This is important because then we don't need forward declarations
# when generating C and C++ code (plus it also makes the size-tests
# easier to debug)
def sort_structs_map(structs_map):

    # From Python 3.6 onwards, the standard dict type maintains insertion order by default.
    sorted_class_map = OrderedDict([])
    # when encountering the class "DomVec", you must forward-declare the class "Dom"
    forward_delcarations = OrderedDict([("DomVec", "Dom")])
    classes_not_found = OrderedDict([])

    # first, insert all types that only have primitive types as fields
    for class_name in structs_map.keys():
        clazz = structs_map[class_name]
        should_insert_struct = True

        found_c_is_callback_typedef = "callback_typedef" in clazz.keys() and (len(clazz["callback_typedef"].keys()) > 0)
        found_c_is_boxed_object = "is_boxed_object" in clazz.keys() and clazz["is_boxed_object"]

        if found_c_is_callback_typedef or found_c_is_boxed_object:
            pass
        elif "struct" in clazz.keys():
            struct = clazz["struct"]
            for field in struct:
                field_name = list(field.keys())[0]
                field_type = list(field.values())[0]
                field_type = prefix + analyze_type(field_type["type"])[1]
                if not(is_primitive_arg(field_type)):
                    should_insert_struct = False
        elif "enum" in clazz.keys():
            enum = clazz["enum"]
            for variant in enum:
                variant_name = list(variant.keys())[0]
                variant_type = list(variant.values())[0]
                if "type" in variant_type.keys():
                    variant_type = prefix + analyze_type(variant_type["type"])[1]
                    if not(is_primitive_arg(variant_type)):
                        should_insert_struct = False
        else:
            raise Exception("sort_structs_map: not enum nor struct nor typedef" + class_name + "")

        if should_insert_struct:
            sorted_class_map[class_name] = clazz
        else:
            classes_not_found[class_name] = clazz

    # Now loop through every class that was not a primitive type
    # usually this should resolve in 9 - 10 iterations
    iteration_count = 0;
    while not(len(classes_not_found.keys()) == 0):
        # classes not found in this iteration
        current_classes_not_found = OrderedDict([])

        for class_name in classes_not_found.keys():
            clazz = classes_not_found[class_name]
            should_insert_struct = True
            found_c_is_callback_typedef = "callback_typedef" in clazz.keys() and (len(clazz["callback_typedef"].keys()) > 0)

            if found_c_is_callback_typedef:
                pass
            elif "struct" in clazz.keys():
                struct = clazz["struct"]
                for field in struct:
                    field_name = list(field.keys())[0]
                    field_type = list(field.values())[0]
                    field_type = analyze_type(field_type["type"])[1]
                    if not(is_primitive_arg(field_type)) and not(field_type in forward_delcarations.keys()):
                        field_type = prefix + field_type
                        if not(field_type in sorted_class_map.keys()):
                            should_insert_struct = False
            elif "enum" in clazz.keys():
                enum = clazz["enum"]
                for variant in enum:
                    variant_name = list(variant.keys())[0]
                    variant_type = list(variant.values())[0]
                    if "type" in variant_type.keys():
                        variant_type = analyze_type(variant_type["type"])[1]
                        if not(is_primitive_arg(variant_type)) and not(variant_type in forward_delcarations.keys()):
                            variant_type = prefix + variant_type
                            if not(variant_type in sorted_class_map.keys()):
                                should_insert_struct = False
            else:
                raise Exception("sort_structs_map: not enum nor struct " + class_name + "")

            if should_insert_struct:
                sorted_class_map[class_name] = clazz
            else:
                current_classes_not_found[class_name] = clazz


        classes_not_found = current_classes_not_found
        iteration_count += 1

        # NOTE: if the iteration count is extremely high,
        # something is wrong with the script
        if iteration_count > 500:
            raise Exception("infinite recursion detected in sort_structs_map: " + str(len(current_classes_not_found.keys())) + " unresolved structs = " + str(current_classes_not_found.keys()) + "\r\n")

    return [sorted_class_map, forward_delcarations]

# Generate the RUST code for the struct layout of the final API
# This function has to be called twice in order to ensure that the layout of the struct
# matches the layout in the binary
def generate_structs(api_data, structs_map, autoderive):

    code = ""

    for struct_name in structs_map.keys():
        struct = structs_map[struct_name]

        if "doc" in struct.keys():
            code += "    /// " + struct["doc"] + "\r\n"
        else:
            code += "    /// `" + struct_name + "` struct\r\n"

        class_is_callback_typedef = "callback_typedef" in struct.keys() and (len(struct["callback_typedef"].keys()) > 0)
        class_can_be_copied = "derive" in struct.keys() and "Copy" in struct["derive"]
        class_has_custom_destructor = "custom_destructor" in struct.keys() and struct["custom_destructor"]
        class_can_be_cloned = True
        if "clone" in struct.keys():
            class_can_be_cloned = struct["clone"]

        is_boxed_object = "is_boxed_object" in struct.keys() and struct["is_boxed_object"]
        treat_external_as_ptr = "external" in struct.keys() and is_boxed_object

        if class_is_callback_typedef:
            fn_ptr = generate_rust_callback_fn_type(api_data, struct["callback_typedef"])
            code += "    pub type " + struct_name + " = " + fn_ptr + ";\r\n"
        elif "struct" in struct.keys():
            struct = struct["struct"]

            # for LayoutCallback and RefAny, etc. the #[derive(Debug)] has to be implemented manually
            opt_derive_debug = "#[derive(Debug)]"
            opt_derive_clone = "#[derive(Clone)]"
            opt_derive_copy = "#[derive(Copy)]"
            opt_derive_other = "#[derive(PartialEq, PartialOrd)]"

            if not(class_can_be_copied):
                opt_derive_copy = ""

            if not(class_can_be_cloned) or (treat_external_as_ptr and class_can_be_cloned):
                opt_derive_clone = ""

            if class_has_custom_destructor or not(autoderive) or struct_name == "AzRefCount" or struct_name == "AzU8VecRef":
                opt_derive_copy = ""
                opt_derive_debug = ""
                opt_derive_clone = ""
                opt_derive_other = ""

            for field in struct:
                if "type" in list(field.values())[0]:
                    analyzed_arg_type = analyze_type(list(field.values())[0]["type"])
                    if not(is_primitive_arg(analyzed_arg_type[1])):
                        field_type_class_path = search_for_class_by_class_name(api_data, analyzed_arg_type[1])
                        if field_type_class_path is None:
                            print("no field_type_class_path found for " + str(analyzed_arg_type))
                        found_c = get_class(api_data, field_type_class_path[0], field_type_class_path[1])
                        found_c_is_callback_typedef = "callback_typedef" in found_c.keys() and found_c["callback_typedef"]
                        if found_c_is_callback_typedef:
                            opt_derive_debug = ""
                            opt_derive_other = ""

            code += "    #[repr(C)] "  + opt_derive_debug + " " + opt_derive_clone + " " + opt_derive_other + " " + opt_derive_copy + " pub struct " + struct_name + " {\r\n"

            for field in struct:
                if type(field) is str:
                    print("Struct " + struct_name + " should have a dictionary as fields")
                field_name = list(field.keys())[0]
                field_type = list(field.values())[0]
                if "type" in field_type:
                    field_type = field_type["type"]
                    analyzed_arg_type = analyze_type(field_type)
                    if is_primitive_arg(analyzed_arg_type[1]):
                        if field_name == "ptr":
                            code += "        pub(crate) "
                        else:
                            code += "        pub "
                        code += field_name + ": " + field_type + ",\r\n"
                    else:
                        field_type_class_path = search_for_class_by_class_name(api_data, analyzed_arg_type[1])
                        if field_type_class_path is None:
                            print("no field_type_class_path found for " + str(analyzed_arg_type))

                        found_c = get_class(api_data, field_type_class_path[0], field_type_class_path[1])
                        if field_name == "ptr":
                            code += "        pub(crate) "
                        else:
                            code += "        pub "
                        code += field_name + ": " + analyzed_arg_type[0] + prefix + field_type_class_path[1] + analyzed_arg_type[2] + ",\r\n"
                else:
                    print("struct " + struct_name + " does not have a type on field " + field_name)
                    raise Exception("error")
            code += "    }\r\n"
        elif "enum" in struct.keys():
            enum = struct["enum"]
            repr = "#[repr(C)]"
            for variant in enum:
                variant_name = list(variant.keys())[0]
                variant = list(variant.values())[0]
                if "type" in variant.keys():
                    repr = "#[repr(C, u8)]"

            # don't derive(Debug) for enums with function pointers in their variants
            opt_derive_debug = "#[derive(Debug)]"
            opt_derive_clone = "#[derive(Clone)]"
            opt_derive_copy = "#[derive(Copy)]"
            opt_derive_other = "#[derive(PartialEq, PartialOrd)]"

            if not(class_can_be_copied):
                opt_derive_copy = ""

            if not(class_can_be_cloned) or (treat_external_as_ptr and class_can_be_cloned):
                opt_derive_clone = ""

            if class_has_custom_destructor or not(autoderive):
                opt_derive_copy = ""
                opt_derive_debug = ""
                opt_derive_clone = ""
                opt_derive_other = ""

            for variant in enum:
                variant = list(variant.values())[0]
                if "type" in variant.keys():
                    variant_type = variant["type"]
                    analyzed_arg_type = analyze_type(variant_type)
                    if not(is_primitive_arg(analyzed_arg_type[1])):
                        field_type_class_path = search_for_class_by_class_name(api_data, analyzed_arg_type[1])
                        if field_type_class_path is None:
                            print("no field_type_class_path found for " + str(analyzed_arg_type))
                        found_c = get_class(api_data, field_type_class_path[0], field_type_class_path[1])
                        found_c_is_callback_typedef = "callback_typedef" in found_c.keys() and found_c["callback_typedef"]
                        if found_c_is_callback_typedef:
                            opt_derive_debug = ""
                            opt_derive_other = ""

            code += "    " + repr + " " + opt_derive_debug + " " + opt_derive_clone + " " + opt_derive_other + " " + opt_derive_copy + " pub enum " + struct_name + " {\r\n"
            for variant in enum:
                variant_name = list(variant.keys())[0]
                variant = list(variant.values())[0]
                if "type" in variant.keys():
                    variant_type = variant["type"]
                    if is_primitive_arg(variant_type):
                        code += "        " + variant_name + "(" + variant_type + "),\r\n"
                    else:
                        analyzed_arg_type = analyze_type(variant_type)
                        field_type_class_path = search_for_class_by_class_name(api_data, analyzed_arg_type[1])
                        if field_type_class_path is None:
                            print("variant_type not found: " + variant_type + " in " + struct_name)
                        found_c = get_class(api_data, field_type_class_path[0], field_type_class_path[1])
                        code += "        " + variant_name + "(" + analyzed_arg_type[0] + prefix + field_type_class_path[1] + analyzed_arg_type[2] + "),\r\n"
                else:
                    code += "        " + variant_name + ",\r\n"
            code += "    }\r\n"

    return code

# returns the RUST DLL binding code
def generate_rust_dll_bindings(api_data, structs_map, functions_map):

    code = ""

    code += read_file(root_folder + "/api/_patches/azul.rs/dll.rs")

    code += "    #[cfg(not(feature = \"link_static\"))]"
    code += "    mod dynamic_link {\r\n"
    code += "    use core::ffi::c_void;\r\n\r\n"

    code += generate_structs(api_data, structs_map, True)

    code += "    #[cfg_attr(target_os = \"windows\", link(name=\"azul.dll\"))] // https://github.com/rust-lang/cargo/issues/9082\r\n"
    code += "    #[cfg_attr(not(target_os = \"windows\"), link(name=\"azul\"))] // https://github.com/rust-lang/cargo/issues/9082\r\n"
    code += "    extern \"C\" {\r\n"

    for fn_name in functions_map.keys():
        fn_type = functions_map[fn_name]
        fn_args = fn_type[0]
        fn_return = fn_type[1]
        return_arrow = "" if fn_return == "" else " -> "
        code += "        pub(crate) fn " + fn_name + "(" + strip_fn_arg_types(fn_args) + ")" + return_arrow + fn_return + ";\r\n"

    code += "    }\r\n\r\n"

    code += "    }\r\n\r\n"
    code += "    #[cfg(not(feature = \"link_static\"))]\r\n"
    code += "    pub use self::dynamic_link::*;\r\n"


    code += "\r\n"
    code += "\r\n"

    code += "    #[cfg(feature = \"link_static\")]\r\n"
    code += "    mod static_link {\r\n"
    code += "       #[cfg(feature = \"link_static\")]\r\n"
    code += "        extern crate azul_dll;\r\n"
    code += "       #[cfg(feature = \"link_static\")]\r\n"
    code += "        use azul_dll::*;\r\n"
    code += "    }\r\n\r\n"
    code += "    #[cfg(feature = \"link_static\")]\r\n"
    code += "    pub use self::static_link::*;\r\n"

    return code

# Generates the azul/rust/azul.rs file
def generate_rust_api(api_data, structs_map, functions_map):

    module_file_map = {}
    version = list(api_data.keys())[-1]
    module_file_map['dll'] = generate_rust_dll_bindings(api_data[version], structs_map, functions_map)
    api_data = api_data[version]

    for module_name in api_data.keys():
        code = ""
        module_doc = None
        if "doc" in api_data[module_name]:
            module_doc = api_data[module_name]["doc"]

        module = api_data[module_name]["classes"]

        code += "    #![allow(dead_code, unused_imports)]\r\n"
        if module_doc != None:
            code += "    //! " + module_doc + "\r\n"
        code += "    use crate::dll::*;\r\n"
        code += "    use core::ffi::c_void;\r\n"

        if tuple([module_name]) in rust_api_patches:
            code += rust_api_patches[tuple([module_name])]

        code += get_all_imports(api_data, module, module_name)

        for class_name in module.keys():
            c = module[class_name]

            class_can_derive_debug = "derive" in c.keys() and "Debug" in c["derive"]
            class_can_be_copied = "derive" in c.keys() and "Copy" in c["derive"]
            class_has_partialeq = "derive" in c.keys() and "PartialEq" in c["derive"]
            class_has_eq = "derive" in c.keys() and "Eq" in c["derive"]
            class_has_partialord = "derive" in c.keys() and "PartialOrd" in c["derive"]
            class_has_ord = "derive" in c.keys() and "Ord" in c["derive"]
            class_can_be_hashed = "derive" in c.keys() and "Hash" in c["derive"]

            class_is_boxed_object = not(class_is_stack_allocated(c))
            class_is_const = "const" in c.keys()
            class_is_callback_typedef = "callback_typedef" in c.keys() and (len(c["callback_typedef"]) > 0)
            class_has_custom_destructor = "custom_destructor" in c.keys() and c["custom_destructor"]
            treat_external_as_ptr = "external" in c.keys() and "is_boxed_object" in c.keys() and c["is_boxed_object"]

            class_can_be_cloned = True
            if "clone" in c.keys():
                class_can_be_cloned = c["clone"]

            c_is_stack_allocated = not(class_is_boxed_object)
            class_ptr_name = prefix + class_name
            if c_is_stack_allocated:
                class_ptr_name = prefix + class_name

            if "doc" in c.keys():
                code += "    /// " + c["doc"] + "\r\n    "
            else:
                code += "    /// `" + class_name + "` struct\r\n    "

            code += "\r\n#[doc(inline)] pub use crate::dll::" + class_ptr_name + " as " + class_name + ";\r\n"

            should_emit_impl = not(class_is_const or class_is_callback_typedef) and (("constructors" in c.keys() and len(c["constructors"]) > 0) or ("functions" in c.keys() and len(c["functions"]) > 0))
            if should_emit_impl:
                code += "    impl " + class_name + " {\r\n"

                if "constructors" in c.keys():
                    for fn_name in c["constructors"]:
                        const = c["constructors"][fn_name]

                        c_fn_name = to_snake_case(class_ptr_name) + "_" + fn_name
                        fn_args = rust_bindings_fn_args(const, class_name, class_ptr_name, False, api_data)
                        fn_args_call = rust_bindings_call_fn_args(const, class_name, class_ptr_name, False, api_data, class_is_boxed_object)

                        fn_body = ""

                        if tuple([module_name, class_name, fn_name]) in rust_api_patches.keys() \
                        and "use_patches" in const.keys() \
                        and "rust" in const["use_patches"]:
                            fn_body = rust_api_patches[tuple([module_name, class_name, fn_name])]
                        else:
                            fn_body = "unsafe { crate::dll::" + c_fn_name + "(" + fn_args_call + ") }"

                        if "doc" in const.keys():
                            code += "        /// " + const["doc"] + "\r\n"
                        else:
                            code += "        /// Creates a new `" + class_name + "` instance.\r\n"

                        returns = "Self"
                        if "returns" in const.keys():
                            return_type = const["returns"]["type"]
                            returns = return_type
                            analyzed_return_type = analyze_type(return_type)
                            if is_primitive_arg(analyzed_return_type[1]):
                                fn_body = fn_body
                            else:
                                return_type_class = search_for_class_by_class_name(api_data, analyzed_return_type[1])
                                if return_type_class is None:
                                    print("no return type found for return type: " + return_type)
                                returns = analyzed_return_type[0] + " crate::" + return_type_class[0] + "::" + return_type_class[1] + analyzed_return_type[2]
                                fn_body = fn_body

                        code += "        pub fn " + fn_name + "(" + fn_args + ") -> " + returns + " { " + fn_body + " }\r\n"

                if "functions" in c.keys():
                    for fn_name in c["functions"]:
                        f = c["functions"][fn_name]

                        fn_args = rust_bindings_fn_args(f, class_name, class_ptr_name, True, api_data)
                        fn_args_call = rust_bindings_call_fn_args(f, class_name, class_ptr_name, True, api_data, class_is_boxed_object)
                        c_fn_name = to_snake_case(class_ptr_name) + "_" + fn_name

                        fn_body = ""

                        if tuple([module_name, class_name, fn_name]) in rust_api_patches.keys() \
                        and "use_patches" in const.keys() \
                        and "rust" in const["use_patches"]:
                            fn_body = rust_api_patches[tuple([module_name, class_name, fn_name])]
                        else:
                            fn_body = "unsafe { crate::dll::" + c_fn_name + "(" + fn_args_call + ") }"

                        if tuple([module_name, class_name, fn_name]) in rust_api_patches:
                            code += rust_api_patches[tuple([module_name, class_name, fn_name])]
                            if "use_patches" in f.keys() and f["use_patches"]:
                                continue

                        if "doc" in f.keys():
                            code += "        /// " + f["doc"] + "\r\n"
                        else:
                            code += "        /// Calls the `" + class_name + "::" + fn_name + "` function.\r\n"

                        returns = ""
                        if "returns" in f.keys():
                            return_type = f["returns"]["type"]
                            returns = " -> " + return_type
                            analyzed_return_type = analyze_type(return_type)
                            if is_primitive_arg(analyzed_return_type[1]):
                                fn_body = fn_body
                            else:
                                return_type_class = search_for_class_by_class_name(api_data, analyzed_return_type[1])
                                if return_type_class is None:
                                    print("no return type found for return type: " + return_type)
                                returns = " ->" + analyzed_return_type[0] + " crate::" + return_type_class[0] + "::" + return_type_class[1] + analyzed_return_type[2]
                                fn_body = fn_body

                        code += "        pub fn " + fn_name + "(" + fn_args + ") " +  returns + " { " + fn_body + " }\r\n"

                code += "    }\r\n\r\n" # end of class

            if treat_external_as_ptr and class_can_be_cloned:
                code += "    impl Clone for " + class_name + " { fn clone(&self) -> Self { unsafe { crate::dll::" + to_snake_case(class_ptr_name) + "_deep_copy(self) } } }\r\n"
            if treat_external_as_ptr:
                code += "    impl Drop for " + class_name + " { fn drop(&mut self) { unsafe { crate::dll::" + to_snake_case(class_ptr_name) + "_delete(self) } } }\r\n"


        module_file_map[module_name] = code

    final_code = ""

    for line in license.splitlines():
        final_code += "// " + line + "\r\n"

    final_code += read_file(root_folder + "/api/_patches/azul.rs/header.rs")

    for module_name in module_file_map.keys():
        if module_name != "dll":
            final_code += "pub "
        final_code += "mod " + module_name + " {\r\n"
        final_code += module_file_map[module_name]
        final_code += "}\r\n\r\n"

    return final_code

# Generate the RUST function callback type:
#
# extern "C" fn(&Blah, &Foo) -> FooReturn
def generate_rust_callback_fn_type(api_data, callback_typedef):
    # callback_typedef

    fn_string = "extern \"C\" fn("

    if "fn_args" in callback_typedef.keys():
        fn_args = callback_typedef["fn_args"]
        for fn_arg in fn_args:
            fn_arg_type = fn_arg["type"]
            fn_arg_ref = fn_arg["ref"]
            search_result = search_for_class_by_class_name(api_data, fn_arg_type)
            fn_arg_class = fn_arg_type
            if not(is_primitive_arg(fn_arg_type)):
                if search_result is None:
                    print("fn_arg_type " + fn_arg_type + " not found!")
                fn_arg_class = search_result[1]

            if not(is_primitive_arg(fn_arg_type)):
                if fn_arg_ref == "ref":
                    fn_string += "&" + prefix + fn_arg_class
                elif fn_arg_ref == "refmut":
                    fn_string += "&mut " + prefix + fn_arg_class
                elif fn_arg_ref == "value":
                    fn_string += prefix + fn_arg_class
                else:
                    raise Exception("wrong fn_arg_ref on " + fn_arg_type)
            else:
                if fn_arg_ref == "ref":
                    fn_string += "&"  + fn_arg_class
                elif fn_arg_ref == "refmut":
                    fn_string += "&mut " + fn_arg_class
                elif fn_arg_ref == "value":
                    fn_string += fn_arg_class
                else:
                    raise Exception("wrong fn_arg_ref on " + fn_arg_type)

            fn_string += ", "

        if len(fn_args) > 0:
            fn_string = fn_string[:-2] # trim last comma

    fn_string += ")"

    if "returns" in callback_typedef.keys():
        fn_string += " -> "
        fn_arg_type = callback_typedef["returns"]["type"]
        search_result = search_for_class_by_class_name(api_data, fn_arg_type)
        fn_arg_class = fn_arg_type

        if not(is_primitive_arg(fn_arg_type)):
            if search_result is None:
                print("fn_arg_type " + fn_arg_type + " not found!")
                raise Exception("fn_arg_type " + fn_arg_type + " not found!")
            fn_arg_class = search_result[1]

        if not(is_primitive_arg(fn_arg_type)):
            fn_string += prefix + fn_arg_class
        else:
            fn_string += fn_arg_class

    return fn_string

def generate_c_api(api_data, structs_map, functions_map):
    pass

def generate_c_callback_fn_type(api_data, callback_typedef):
    pass

# generate a test function that asserts that the struct layout in the DLL
# is the same as in the generated bindings
def generate_size_test(api_data, structs_map):

    generated_structs = generate_structs(api_data, structs_map, False)

    test_str = ""

    test_str += "#[cfg(test)]\r\n"
    test_str += "#[allow(dead_code)]\r\n"
    test_str += "mod test_sizes {\r\n"

    test_str += read_file(root_folder + "/api/_patches/azul-dll/test-sizes.rs")

    test_str += generated_structs
    test_str += "    use core::ffi::c_void;\r\n"
    test_str += "    use azul_impl::css::*;\r\n"
    test_str += "\r\n"

    test_str += "    #[test]\r\n"
    test_str += "    fn test_size() {\r\n"
    test_str += "         use core::alloc::Layout;\r\n"

    for struct_name in structs_map.keys():
        struct = structs_map[struct_name]
        if "external" in struct.keys():
            external_path = struct["external"]
            test_str += "        assert_eq!((Layout::new::<" + external_path + ">(), \"" + struct_name +  "\"), (Layout::new::<" + struct_name + ">(), \"" + struct_name +  "\"));\r\n"

    test_str += "    }\r\n"
    test_str += "}\r\n"
    return test_str

# ---------------------------

def verify_clang_is_installed():
    if not(os.environ['CC'] == 'clang-cl') or not(os.environ['CXX'] == 'clang-cl'):
        raise Exception("environment variables CC and CXX have to be set to 'clang-cl' before building! Make sure LLVM and clang is installed!")

def cleanup_start():
    # TODO: remove entire /api folder and re-generate it?
    files = ["azul.dll", "azul.sh", "azul.dylib"]

    for f in files:
        if os.path.exists(root_folder + "/target/debug/examples/" + f):
            remove_path(root_folder + "/target/debug/examples/" + f)

        if os.path.exists(root_folder + "/target/release/examples/" + f):
            remove_path(root_folder + "/target/release/examples/" + f)

        if os.path.exists(root_folder + "/target/debug/" + f):
            remove_path(root_folder + "/target/debug/" + f)

        if os.path.exists(root_folder + "/target/release/" + f):
            remove_path(root_folder + "/target/release/" + f)

    # if (len(os.environ.get('AZUL_INSTALL_DIR', '')) > 0):
    #     if os.path.exists(os.environ['AZUL_INSTALL_DIR']):
    #         remove_path(os.environ['AZUL_INSTALL_DIR'])

def generate_api():
    apiData = read_api_file(root_folder + "/api.json")
    rust_dll_result = generate_rust_dll(apiData)

    rust_dll_code = rust_dll_result[0]
    structs_map = rust_dll_result[1]
    functions_map = rust_dll_result[2]
    forward_declarations = rust_dll_result[3]

    write_file(rust_dll_code, root_folder + "/azul-dll/src/lib.rs")
    write_file(generate_rust_api(apiData, structs_map, functions_map), root_folder + "/api/rust/lib.rs")
    # write_file(generate_c_api(apiData, structs_map, functions_map, forward_declarations), root_folder + "/api/c/azul.h")
    # write_file(generate_cpp_api(apiData, structs_map, functions_map, forward_declarations), root_folder + "/api/cpp/azul.h")
    # write_file(generate_cpp_api(apiData, structs_map, functions_map, forward_declarations), root_folder + "/api/python/azul.py")

# Build the library with release settings
def build_dll():

    # Make a copy of the current environment
    # d = dict(os.environ)
    # d['CC'] = 'clang'
    # d['CXX'] = 'clang'

    cwd = root_folder + "/azul-dll"

    if platform == "linux" or platform == "linux2": # TODO: freebsd?
        # On linux, optimize for different CPUs, so that we can package for debian multiarch
        os.system('rustup toolchain install nightly-x86_64-unknown-linux-gnu')
        os.system('rustup override set nightly-x86_64-unknown-linux-gnu')
        os.system('RUSTFLAGS="-C target-feature=-crt-static -Zstrip=symbols -Zshare-generics=y" cargo build --target=x86_64-unknown-linux-gnu --all-features --release')
        os.system('rustup toolchain install nightly-i686-unknown-linux-gnu')
        os.system('rustup override set nightly-i686-unknown-linux-gnu')
        os.system('RUSTFLAGS="-C target-feature=-crt-static -Zstrip=symbols -Zshare-generics=y" cargo build --target=i686-unknown-linux-gnu --all-features --release')
    elif platform == "darwin":
        os.system('rustup override set nightly-x86_64-unknown-darwin-gnu', env=d, cwd=cwd).wait()
        os.system('RUSTFLAGS="-C target-feature=-crt-static -Zstrip=symbols -Zshare-generics=y" cargo build --target=x86_64-unknown-darwin-gnu --all-features --release', env=d, cwd=cwd).wait()
        os.system('rustup override set nightly-i686-unknown-darwin-gnu', env=d, cwd=cwd).wait()
        os.system('RUSTFLAGS="-C target-feature=-crt-static -Zstrip=symbols -Zshare-generics=y" cargo build --target=i686-unknown-darwin-gnu --all-features --release', env=d, cwd=cwd).wait()
    elif platform == "win32":
        os.system('rustup override set nightly-x86_64-unknown-windows-msvc', env=d, cwd=cwd).wait()
        os.system('RUSTFLAGS="-C target-feature=-crt-static" cargo build --target=x86_64-unknown-windows-msvc --all-features --release', env=d, cwd=cwd).wait()
        os.system('rustup override set nightly-i686-unknown-windows-msvc', env=d, cwd=cwd).wait()
        os.system('RUSTFLAGS="-C target-feature=-crt-static -Zstrip=symbols -Zshare-generics=y" cargo build --target=i686-unknown-windows-msvc --all-features --release', env=d, cwd=cwd).wait()
    else:
        raise Exception("unsupported platform: " + platform)

def run_size_test():
    d = dict(os.environ)   # Make a copy of the current environment
    d['CC'] = 'clang-cl'
    d['CXX'] = 'clang-cl'
    d['RUSTFLAGS'] = '-C target-feature=+crt-static'
    cwd = root_folder + "/azul-dll"
    subprocess.Popen(['cargo', 'test', '--all-features', '--release'], env=d, cwd=cwd).wait()

def build_examples():
    cwd = root_folder + "/examples"
    examples = [
        # "async",
        # "calculator",
        # "components",
        # "game_of_life",
        # "headless",
        # "hello_world",
        # "layout_tests",
        # "list",
        # "opengl",
        "public",
        # "heap_corruption_test",
        # "slider",
        # "svg",
        # "table",
        # "text_input",
    ]
    for e in examples:
        subprocess.Popen(['cargo', 'run', '--release', '--bin', e], cwd=cwd).wait()
    pass

def release_on_cargo():
    # Publish packages in the correct order of dedpendencies
    os.system("cd \"" + root_folder + "/azul-css\" && cargo check && cargo test && cargo publish")
    os.system("cd \"" + root_folder + "/azul-css-parser\" && cargo check && cargo test && cargo publish")
    os.system("cd \"" + root_folder + "/azul-core\" && cargo check && cargo test && cargo publish")
    os.system("cd \"" + root_folder + "/azul-text-layout\" && cargo check && cargo test && cargo publish")
    os.system("cd \"" + root_folder + "/azulc\" && cargo check && cargo test && cargo publish")
    os.system("cd \"" + root_folder + "/azul-layout\" && cargo check && cargo test && cargo publish")
    os.system("cd \"" + root_folder + "/azul-desktop\" && cargo check && cargo test && cargo publish")
    os.system("cd \"" + root_folder + "/azul-web\" && cargo check && cargo test && cargo publish")
    os.system("cd \"" + root_folder + "/azul-dll\" && cargo check && cargo test && cargo publish")
    os.system("cd \"" + root_folder + "/azul\" && cargo check && cargo test && cargo publish")
    os.system("cd \"" + root_folder + "/azul-widgets\" && cargo check && cargo test && cargo publish")

def make_debian_release_package():
    # copy the files such that file is Debian deploy-able
    pass

def make_release_zip_files():
    # Generate the [arch]-*x86_64, [arch]-*i686 etc. ZIP files
    pass

def replace_split(d, search, tag):

    newdoc = ""

    split = d.split(search)

    index = 0
    while index < len(split):
        if index % 2 == 0:
            newdoc += split[index]
        else:
            newdoc += "<" + tag + ">" + split[index] + "</" + tag + ">"
        index += 1

    return newdoc

def format_doc(docstring):
    newdoc = docstring
    newdoc = newdoc.replace("<", "&lt;")
    newdoc = newdoc.replace(">", "&gt;")
    newdoc = replace_split(newdoc, "`", "code")
    newdoc = replace_split(newdoc, "**", "strong")
    return newdoc

def generate_docs():
    apiData = read_api_file(root_folder + "/api.json")
    html_template = read_file(root_folder + "/api/_patches/html/api.template.html")

    if os.path.exists(root_folder + "/target/html"):
        remove_path(root_folder + "/target/html")

    if not(os.path.exists(root_folder + "/target")):
        create_folder(root_folder + "/target")

    create_folder(root_folder + "/target/html")
    create_folder(root_folder + "/target/html/api")

    all_versions = list(apiData.keys())

    for version in all_versions:

        api_page_contents = ""
        api_page_contents += "<ul>"

        if "doc" in apiData[version].keys():
            api_page_contents += "<p class=\"version doc\">" + format_doc(apiData[version]["doc"]) + "</p>"

        for module_name in apiData[version].keys():

            api_page_contents += "<li class=\"m\" id=\"m." + module_name + "\">"

            module = apiData[version][module_name]

            if "doc" in module.keys():
                api_page_contents += "<p class=\"m doc\">" + format_doc(module["doc"]) + "</p>"

            api_page_contents += "<h3>mod <a href=\"#m." + module_name + "\">" + module_name + "</a>:</h3>"

            api_page_contents += "<ul>"

            for class_name in module["classes"].keys():
                c = module["classes"][class_name]

                if "doc" in c.keys():
                    api_page_contents += "<p class=\"class doc\">" + format_doc(c["doc"]) + "</p>"

                if "enum_fields" in c.keys():
                    api_page_contents += "<li class=\"st e\" id=\"" + module_name + ".st.e." + class_name + "\">"
                    api_page_contents += "<h4>enum <a href=\"#" + module_name + ".st.e." + class_name + "\">" + class_name + "</a></h4>"
                    for enum_variant in c["enum_fields"]:
                        enum_variant_name = list(enum_variant.keys())[0]
                        if "doc" in enum_variant[enum_variant_name]:
                            api_page_contents += "<p class=\"v doc\">" + format_doc(enum_variant[enum_variant_name]["doc"]) + "</p>"

                        if "type" in enum_variant[enum_variant_name]:
                            enum_variant_type = enum_variant[enum_variant_name]["type"]
                            api_page_contents += "<p class=\"f\">" + enum_variant_name + "(<a href=\"#\">" + enum_variant_type + "</a>)</p>"
                        else:
                            api_page_contents += "<p class=\"f\">" + enum_variant_name + "</p>"

                elif "struct_fields" in c.keys():
                    api_page_contents += "<li class=\"st s\" id=\"" + module_name + ".st.s." + class_name + "\">"
                    api_page_contents += "<h4>struct <a href=\"#" + module_name + ".st.s." + class_name + "\">" + class_name + "</a></h4>"
                    for struct_field in c["struct_fields"]:
                        struct_field_name = list(struct_field.keys())[0]
                        struct_type = struct_field[struct_field_name]["type"]
                        if "doc" in struct_field[struct_field_name]:
                            api_page_contents += "<p class=\"f doc\">" + format_doc(struct_field[struct_field_name]["doc"]) + "</p>"
                        api_page_contents += "<p class=\"f\">" + struct_field_name + ": <a href=\"#\">" + struct_type + "</a></p>"

                elif "callback_typedef" in c.keys():
                    api_page_contents += "<li class=\"fnty\" id=\"" + module_name + ".fnty." + class_name + "\">"
                    api_page_contents += "<h4>fnptr <a href=\"#" + module_name + ".fnty." + class_name + "\">" + class_name + "</a></h4>"
                    callback_typedef = c["callback_typedef"]
                    if "fn_args" in callback_typedef:
                        for fn_arg in callback_typedef["fn_args"]:
                            if "doc" in fn_arg.keys():
                                api_page_contents += "<p class=\"arg doc\">" + format_doc(fn_arg["doc"]) + "</p>"
                            # struct_field_name = list(struct_field.keys())[0]
                            fn_arg_type = fn_arg["type"]
                            fn_arg_ref = fn_arg["ref"]

                            fn_arg_ref_html = ""
                            if (fn_arg_ref == "value"):
                                fn_arg_ref_html = ""
                            elif (fn_arg_ref == "ref"):
                                fn_arg_ref_html = "&"
                            elif (fn_arg_ref == "refmut"):
                                fn_arg_ref_html = "&mut "

                            api_page_contents += "<p class=\"fnty arg\">" + fn_arg_ref_html + " <a href=\"#\">" + fn_arg_type + "</a></p>"
                    if "returns" in callback_typedef:
                        if "doc" in fn_arg.keys():
                            api_page_contents += "<p class=\"ret doc\">" + format_doc(callback_typedef["returns"]["doc"]) + "</p>"
                        api_page_contents += "<p class=\"fnty ret\">-&gt;&nbsp;" + format_doc(callback_typedef["returns"]["type"]) + "</p>"

                if "constructors" in c.keys():
                    api_page_contents += "<ul>"
                    for constructor_name in c["constructors"]:
                        if "doc" in c["constructors"][constructor_name]:
                            api_page_contents += "<p class=\"cn doc\">" + format_doc(c["constructors"][constructor_name]["doc"]) + "</p>"
                        api_page_contents += "<li class=\"cn\" id=\"" + constructor_name + "\"><p>constructor " + constructor_name + "():</p></li>"
                    api_page_contents += "</ul>"

                if "functions" in c.keys():
                    api_page_contents += "<ul>"
                    for function_name in c["functions"]:
                        if "doc" in c["functions"][function_name]:
                            api_page_contents += "<p class=\"fn doc\">" + format_doc(c["functions"][function_name]["doc"]) + "</p>"
                        args = c["functions"][function_name]["fn_args"]
                        arg_string = ""
                        self_arg = ""
                        for arg in args:
                            arg_name = list(arg.keys())[0]
                            arg_val = arg[arg_name]

                            if arg_name == "self":
                                if arg_val == "value":
                                    self_arg = "self"
                                elif arg_val == "ref":
                                    self_arg = "&self"
                                elif arg_val == "refmut":
                                    self_arg = "&mut self"
                            else:
                                if "doc" in arg.keys():
                                    arg_string += "<p class=\"arg doc\">" + doc + "</p>"

                                arg_string += "<p class=\"arg\">arg " + arg_name + ": <a href=\"#\">" + arg_val + "</a></p>"

                        api_page_contents += "<li class=\"fn\" id=\"" + function_name + "\">"
                        api_page_contents += "<p>fn " + function_name + "(" + self_arg + "):</p>"
                        if not(len(arg_string) == 0):
                            api_page_contents += "<ul>"
                            api_page_contents += arg_string
                            api_page_contents += "</ul>"
                        api_page_contents += "</li>"

                    api_page_contents += "</ul>"

                api_page_contents += "</li>"

            api_page_contents += "</ul>"

            api_page_contents += "</li>"

        api_page_contents += "</ul>"

        releases_string = "<ul>"
        for version in all_versions:
            releases_string += "<li><a href=\"./" + version + ".html\">" + version + "</a></li>"
        releases_string += "</ul>"

        final_html = html_template.replace("$$ROOT_RELATIVE$$", "..")
        extra_css = "\
        body > .center > main > div > ul * { font-size: 12px; font-weight: normal; list-style-type: none; font-family: monospace; }\
        body > .center > main > div > ul > li ul { margin-left: 20px; }\
        body > .center > main > div > ul > li.m { margin-top: 40px; margin-bottom: 20px; }\
        body > .center > main > div > ul > li.m > ul > li { margin-bottom: 15px; }\
        body > .center > main > div > ul > li.m > ul > li.st.e { color: #690; }\
        body > .center > main > div > ul > li.m > ul > li.st.s { color: #905; }\
        body > .center > main > div > ul > li.m > ul > li.fnty { color: #ff5722; }\
        body > .center > main > div > ul > li.m > ul > li.st .f { margin-left: 20px; }\
        body > .center > main > div > ul > li.m > ul > li.st .v.doc { margin-left: 20px; }\
        body > .center > main > div > ul > li.m > ul > li.st .cn { margin-left: 20px; color: #07a; }\
        body > .center > main > div > ul > li.m > ul > li.st .fn { margin-left: 20px; color: #004e92; }\
        body > .center > main > div > ul > li.m > ul > li .arg,\
        body > .center > main > div > ul > li.m > ul > li .arg.doc,\
        body > .center > main > div > ul > li.m > ul > li .ret,\
        body > .center > main > div > ul > li.m > ul > li .ret.doc { margin-left: 40px; }\
        body > .center > main > div p.doc { margin-top: 5px !important; color: black !important; max-width: 70ch !important; font-weight: bolder; }\
        body > .center > main > div a { color: inherit !important; }\
        "
        final_html = final_html.replace("/*$$_EXTRA_CSS$$*/", extra_css)
        final_html = final_html.replace("$$SIDEBAR_GUIDE$$", "")
        final_html = final_html.replace("$$SIDEBAR_CLASSES$$", releases_string)
        final_html = final_html.replace("$$TITLE$$", "v" + version)
        final_html = final_html.replace("$$CONTENT$$", api_page_contents)
        write_file(final_html, root_folder + "/target/html/api/" + version + ".html")

    releases_string = "<ul>"
    for version in all_versions:
        releases_string += "<li><a href=\"./api/" + version + ".html\">" + version + "</a></li>"
    releases_string += "</ul>"

    api_combined_page = html_template.replace("$$ROOT_RELATIVE$$", ".")
    api_combined_page = api_combined_page.replace("$$SIDEBAR_GUIDE$$", "")
    api_combined_page = api_combined_page.replace("$$SIDEBAR_CLASSES$$", releases_string)
    api_combined_page = api_combined_page.replace("$$TITLE$$", "Choose API version")
    api_combined_page = api_combined_page.replace("$$CONTENT$$", releases_string)
    write_file(api_combined_page, root_folder + "/target/html/api.html")

def build_azulc():
    # enable features="image_loading, font_loading" to enable layouting
    os.system('cd "' + root_folder + '/azulc" && RUSTFLAGS="-Ctarget-feature=-crt-static -Zstrip=symbols -Zshare-generics=y" cargo +nightly build --bin azulc --no-default-features --features="xml std font_loading image_loading text_layout" --release')

def full_test():
    os.system('cd "' + root_folder + '/azul-dll" && cargo check --verbose --all-features')
    os.system('cd "' + root_folder + '/azul-dll" && cargo check --verbose --examples')
    os.system('cd "' + root_folder + '/azul-dll" && cargo check --no-default-features')
    os.system('cd "' + root_folder + '/azul-dll" && cargo check --verbose --release --all-features')
    os.system('cd "' + root_folder + '/azul-dll" && cargo check --no-default-features --features="svg"')
    os.system('cd "' + root_folder + '/azul-dll" && cargo check --no-default-features --features="image_loading"')
    os.system('cd "' + root_folder + '/azul-dll" && cargo check --no-default-features --features="font_loading"')
    os.system('cd "' + root_folder + '/azul-dll" && cargo test --verbose --all-features')
    os.system('cd "' + root_folder + "/examples && cargo run --bin layout_tests -- --nocapture")

def main():
    print("removing old azul.dll...")
    cleanup_start()
    # print("verifying that LLVM / clang-cl is installed...")
    # verify_clang_is_installed()
    print("generating API...")
    generate_api()
    print("generating documentation in /target/html...")
    generate_docs()
    print("building azulc (release mode)...")
    # build_azulc()
    print("building azul-dll (release mode)...")
    # build_dll()
    print("checking azul-dll for struct size integrity...")
    # run_size_test()
    print("building examples...")
    # build_examples()
    print("building docs (output_dir = /target/doc)...")
    # full_test()
    # release_on_cargo()
    # make_debian_release_package()
    # make_release_zip_files()
    # travis: github release - copy azul.zip!

if __name__ == "__main__":
    main()